"""Sincronización bidireccional entre el nodo local y el servidor central.

- Subida: envía eventos pendientes en lotes acotados (límite de tasa por
  sucursal) y confirma el avance con ``confirmar_hasta()`` sin modificar los
  eventos originales (decisión 0005).
- Bajada: actualiza usuarios locales (incluyendo bajas) y catálogo de
  precios/costos.
- Resiliencia: provee cálculo de backoff exponencial con jitter para
  reintentos cuando central no está disponible.
"""

import random
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pumaverso_contratos import EventoContrato

from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.eventos import (
    Evento,
    confirmar_hasta,
    eventos_pendientes,
    ultimo_consecutivo,
    ultimo_consecutivo_confirmado,
)
from pumaverso_tienda.persistencia.transacciones import transaccion
from pumaverso_tienda.usuarios import guardar_usuario_local


class ErrorSincronizacion(RuntimeError):
    """Fallo de comunicación o respuesta inválida del servidor central."""


@dataclass(frozen=True, slots=True)
class RespuestaSubida:
    """Confirmación devuelta por central tras recibir un lote de eventos."""

    ultimo_consecutivo_confirmado: int
    hueco_detectado_desde: int | None = None


@dataclass(frozen=True, slots=True)
class ActualizacionUsuario:
    """Credencial o baja de usuario descargada desde central."""

    usuario_id: str
    nombre: str
    rol: str
    hash_pin: str
    activo: bool
    actualizado_en: datetime


@dataclass(frozen=True, slots=True)
class ActualizacionProducto:
    """Precio y costo de producto descargados desde central."""

    sku: str
    nombre: str
    precio_centavos: int
    precio_actualizado_en: datetime
    costo_centavos: int
    costo_actualizado_en: datetime
    activo: bool = True


@dataclass(frozen=True, slots=True)
class PaqueteBajada:
    """Datos maestros enviados por central hacia la sucursal."""

    usuarios: tuple[ActualizacionUsuario, ...] = ()
    productos: tuple[ActualizacionProducto, ...] = ()


@dataclass(frozen=True, slots=True)
class ResultadoSincronizacion:
    """Resumen de un ciclo de sincronización."""

    eventos_enviados: int
    ultimo_consecutivo_confirmado: int
    usuarios_actualizados: int
    productos_actualizados: int
    hueco_detectado_desde: int | None = None


class TransporteCentral(Protocol):
    """Interfaz de transporte hacia central (HTTP real o doble en pruebas)."""

    def subir_eventos(
        self, sucursal_id: str, caja_id: str, eventos: list[EventoContrato]
    ) -> RespuestaSubida: ...

    def descargar_actualizaciones(
        self, sucursal_id: str, desde: datetime | None
    ) -> PaqueteBajada: ...


class TransporteCentralHttp:
    """Cliente HTTP síncrono contra la API del servidor central."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def subir_eventos(
        self, sucursal_id: str, caja_id: str, eventos: list[EventoContrato]
    ) -> RespuestaSubida:
        payload = {
            "sucursal_id": sucursal_id,
            "caja_id": caja_id,
            "eventos": [e.model_dump(mode="json") for e in eventos],
        }
        try:
            respuesta = httpx.post(
                f"{self._base_url}/sincronizacion/eventos",
                json=payload,
                timeout=self._timeout,
            )
            respuesta.raise_for_status()
        except httpx.HTTPError as error:
            raise ErrorSincronizacion(f"Error al subir eventos a central: {error}") from error

        datos = respuesta.json()
        return RespuestaSubida(
            ultimo_consecutivo_confirmado=int(datos["ultimo_consecutivo_confirmado"]),
            hueco_detectado_desde=datos.get("hueco_detectado_desde"),
        )

    def descargar_actualizaciones(
        self, sucursal_id: str, desde: datetime | None
    ) -> PaqueteBajada:
        params: dict[str, str] = {"sucursal_id": sucursal_id}
        if desde is not None:
            params["desde"] = desde.astimezone(UTC).isoformat()
        try:
            respuesta = httpx.get(
                f"{self._base_url}/sincronizacion/actualizaciones",
                params=params,
                timeout=self._timeout,
            )
            respuesta.raise_for_status()
        except httpx.HTTPError as error:
            raise ErrorSincronizacion(
                f"Error al descargar actualizaciones de central: {error}"
            ) from error

        datos: dict[str, Any] = respuesta.json()
        usuarios = tuple(
            ActualizacionUsuario(
                usuario_id=u["usuario_id"],
                nombre=u["nombre"],
                rol=u["rol"],
                hash_pin=u["hash_pin"],
                activo=bool(u["activo"]),
                actualizado_en=datetime.fromisoformat(u["actualizado_en"]),
            )
            for u in datos.get("usuarios", [])
        )
        productos = tuple(
            ActualizacionProducto(
                sku=p["sku"],
                nombre=p["nombre"],
                precio_centavos=int(p["precio_centavos"]),
                precio_actualizado_en=datetime.fromisoformat(p["precio_actualizado_en"]),
                costo_centavos=int(p["costo_centavos"]),
                costo_actualizado_en=datetime.fromisoformat(p["costo_actualizado_en"]),
                activo=bool(p.get("activo", True)),
            )
            for p in datos.get("productos", [])
        )
        return PaqueteBajada(usuarios=usuarios, productos=productos)


def calcular_espera_reintento(
    intento: int,
    *,
    base_segundos: float = 1.0,
    max_segundos: float = 60.0,
    rng: random.Random | None = None,
) -> float:
    """Calcula espera en segundos con backoff exponencial y full jitter."""
    if intento < 1:
        raise ValueError("intento debe ser mayor o igual a 1.")
    techo = min(max_segundos, base_segundos * (2 ** (intento - 1)))
    generador = rng if rng is not None else random.SystemRandom()
    return generador.uniform(0.0, techo)


def sincronizar_nodo(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    transporte: TransporteCentral,
    limite_lote: int = 100,
    ahora: datetime | None = None,
) -> ResultadoSincronizacion:
    """Ejecuta un ciclo de subida de eventos pendientes y bajada de catálogo/usuarios."""
    referencia = _asegurar_zona_horaria(ahora)
    pendientes = eventos_pendientes(conexion, sucursal_id, caja_id, limite=limite_lote)
    confirmado = ultimo_consecutivo_confirmado(conexion, sucursal_id, caja_id)
    hueco: int | None = None

    if pendientes:
        contratos = [_evento_a_contrato(e) for e in pendientes]
        respuesta = transporte.subir_eventos(sucursal_id, caja_id, contratos)
        hueco = respuesta.hueco_detectado_desde
        if respuesta.ultimo_consecutivo_confirmado > confirmado:
            confirmar_hasta(
                conexion,
                sucursal_id,
                caja_id,
                respuesta.ultimo_consecutivo_confirmado,
                ahora=referencia,
            )
            confirmado = respuesta.ultimo_consecutivo_confirmado

    bajada = transporte.descargar_actualizaciones(sucursal_id, desde=None)
    if bajada.usuarios or bajada.productos:
        with transaccion(conexion):
            for usuario in bajada.usuarios:
                guardar_usuario_local(
                    conexion,
                    usuario_id=usuario.usuario_id,
                    nombre=usuario.nombre,
                    rol=usuario.rol,
                    hash_pin=usuario.hash_pin,
                    activo=usuario.activo,
                    actualizado_en=usuario.actualizado_en,
                )
            for producto in bajada.productos:
                guardar_producto(
                    conexion,
                    sku=producto.sku,
                    nombre=producto.nombre,
                    precio_centavos=producto.precio_centavos,
                    precio_actualizado_en=producto.precio_actualizado_en,
                    costo_centavos=producto.costo_centavos,
                    costo_actualizado_en=producto.costo_actualizado_en,
                    activo=producto.activo,
                )

    return ResultadoSincronizacion(
        eventos_enviados=len(pendientes),
        ultimo_consecutivo_confirmado=confirmado,
        usuarios_actualizados=len(bajada.usuarios),
        productos_actualizados=len(bajada.productos),
        hueco_detectado_desde=hueco,
    )


def estado_sincronizacion_caja(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str
) -> dict[str, int]:
    """Devuelve métricas actuales de sincronización de la caja."""
    ultimo = ultimo_consecutivo(conexion, sucursal_id, caja_id)
    confirmado = ultimo_consecutivo_confirmado(conexion, sucursal_id, caja_id)
    return {
        "ultimo_consecutivo": ultimo,
        "ultimo_consecutivo_confirmado": confirmado,
        "pendientes": max(0, ultimo - confirmado),
    }


def _evento_a_contrato(evento: Evento) -> EventoContrato:
    return EventoContrato(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo,
        version_esquema=evento.version_esquema,
        contenido=evento.contenido,
        usuario_id=evento.usuario_id,
        registrado_en=datetime.fromisoformat(evento.registrado_en),
        hash=evento.hash,
    )


def _asegurar_zona_horaria(ahora: datetime | None) -> datetime:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha debe incluir zona horaria.")
    return momento.astimezone(UTC)
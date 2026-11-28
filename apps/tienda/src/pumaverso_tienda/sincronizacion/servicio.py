"""Sincronización bidireccional entre el nodo local y el servidor central.

- Subida: envía eventos pendientes en lotes acotados (límite de tasa por
  sucursal) y confirma el avance con ``confirmar_hasta()`` sin modificar los
  eventos originales (decisión 0005).
- Bajada: actualiza usuarios locales (incluyendo bajas) y catálogo de
  precios/costos.
- Resiliencia: provee cálculo de backoff exponencial con jitter para
  reintentos cuando central no está disponible.

Orden de un ciclo (decisión 0006): primero la bajada y después la subida. Así,
aunque central rechace un evento, la tienda sigue recibiendo bajas de personal
y precios, y no se bloquea al vencer las vigencias.
"""

import random
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pumaverso_contratos import EventoContrato

from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.eventos import (
    ConfirmacionInvalidaError,
    Evento,
    confirmar_hasta,
    eventos_pendientes,
    ultimo_consecutivo,
    ultimo_consecutivo_confirmado,
)
from pumaverso_tienda.persistencia.transacciones import transaccion
from pumaverso_tienda.sincronizacion.reloj import registrar_bajada
from pumaverso_tienda.usuarios import guardar_usuario_local

# Una sola sincronización a la vez en este proceso: el trabajador automático y
# el endpoint manual podrían coincidir y confirmar consecutivos fuera de orden.
_CANDADO_SINCRONIZACION = threading.Lock()


class ErrorSincronizacion(RuntimeError):
    """Fallo de comunicación o respuesta inválida del servidor central."""


class ConflictoSincronizacionError(ErrorSincronizacion):
    """Central rechazó los eventos de esta caja porque no coinciden con los que ya tiene.

    Suele ocurrir si la caja se reinstaló con una base de datos nueva y la misma
    SUCURSAL_ID/CAJA_ID. Reintentar no lo arregla: requiere revisión.
    """


class CredencialRechazadaError(ConflictoSincronizacionError):
    """Central no aceptó la llave de esta sucursal (falta, es incorrecta o es de otra).

    Reintentar no lo arregla: hay que corregir LLAVE_CENTRAL en el .env de la tienda.
    """


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
    """Cliente HTTP síncrono contra la API del servidor central.

    ``cliente`` permite pasar un ``httpx.Client`` propio; las pruebas de punta a
    punta lo usan para hablar con la app de central sin abrir puertos.
    """

    def __init__(
        self,
        base_url: str,
        *,
        llave: str | None = None,
        timeout: float = 10.0,
        cliente: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cliente = cliente
        self._encabezados = {"Authorization": f"Bearer {llave}"} if llave else {}

    def subir_eventos(
        self, sucursal_id: str, caja_id: str, eventos: list[EventoContrato]
    ) -> RespuestaSubida:
        payload = {
            "sucursal_id": sucursal_id,
            "caja_id": caja_id,
            "eventos": [e.model_dump(mode="json") for e in eventos],
        }
        try:
            respuesta = (self._cliente or httpx).post(
                f"{self._base_url}/sincronizacion/eventos",
                json=payload,
                headers=self._encabezados,
                timeout=self._timeout,
            )
        except httpx.HTTPError as error:
            raise ErrorSincronizacion(f"Error al subir eventos a central: {error}") from error
        _rechazar_si_no_acepta_la_llave(respuesta)
        if respuesta.status_code == httpx.codes.CONFLICT:
            raise ConflictoSincronizacionError(
                f"Central rechazó los eventos por conflicto: {_detalle(respuesta)}"
            )
        if respuesta.is_error:
            raise ErrorSincronizacion(
                f"Central respondió {respuesta.status_code} al subir eventos: {_detalle(respuesta)}"
            )

        datos = respuesta.json()
        return RespuestaSubida(
            ultimo_consecutivo_confirmado=int(datos["ultimo_consecutivo_confirmado"]),
            hueco_detectado_desde=datos.get("hueco_detectado_desde"),
        )

    def descargar_actualizaciones(self, sucursal_id: str, desde: datetime | None) -> PaqueteBajada:
        params: dict[str, str] = {"sucursal_id": sucursal_id}
        if desde is not None:
            params["desde"] = desde.astimezone(UTC).isoformat()
        try:
            respuesta = (self._cliente or httpx).get(
                f"{self._base_url}/sincronizacion/actualizaciones",
                params=params,
                headers=self._encabezados,
                timeout=self._timeout,
            )
            _rechazar_si_no_acepta_la_llave(respuesta)
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
    """Ejecuta un ciclo de sincronización: primero bajada y después subida.

    Si la subida falla, la bajada ya quedó guardada y el error se propaga
    (``ErrorSincronizacion`` o ``ConflictoSincronizacionError``).

    Si ya hay otra sincronización en curso en este proceso, espera a que termine.
    """
    with _CANDADO_SINCRONIZACION:
        return _sincronizar(
            conexion,
            sucursal_id=sucursal_id,
            caja_id=caja_id,
            transporte=transporte,
            limite_lote=limite_lote,
            ahora=ahora,
        )


def _sincronizar(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    transporte: TransporteCentral,
    limite_lote: int,
    ahora: datetime | None,
) -> ResultadoSincronizacion:
    referencia = _asegurar_zona_horaria(ahora)

    # 1. Bajada. Siempre completa (desde=None): la vigencia de cada dato se
    #    cuenta desde este momento, así que central debe enviarlos todos.
    bajada = transporte.descargar_actualizaciones(sucursal_id, desde=None)
    with transaccion(conexion):
        for usuario in bajada.usuarios:
            guardar_usuario_local(
                conexion,
                usuario_id=usuario.usuario_id,
                nombre=usuario.nombre,
                rol=usuario.rol,
                hash_pin=usuario.hash_pin,
                activo=usuario.activo,
                actualizado_en=referencia,
            )
        for producto in bajada.productos:
            guardar_producto(
                conexion,
                sku=producto.sku,
                nombre=producto.nombre,
                precio_centavos=producto.precio_centavos,
                precio_actualizado_en=referencia,
                costo_centavos=producto.costo_centavos,
                costo_actualizado_en=referencia,
                activo=producto.activo,
            )
        registrar_bajada(conexion, referencia)

    # 2. Subida de eventos pendientes.
    pendientes = eventos_pendientes(conexion, sucursal_id, caja_id, limite=limite_lote)
    confirmado = ultimo_consecutivo_confirmado(conexion, sucursal_id, caja_id)
    hueco: int | None = None

    if pendientes:
        contratos = [_evento_a_contrato(e) for e in pendientes]
        respuesta = transporte.subir_eventos(sucursal_id, caja_id, contratos)
        hueco = respuesta.hueco_detectado_desde
        if respuesta.ultimo_consecutivo_confirmado > confirmado:
            try:
                confirmar_hasta(
                    conexion,
                    sucursal_id,
                    caja_id,
                    respuesta.ultimo_consecutivo_confirmado,
                    ahora=referencia,
                )
            except ConfirmacionInvalidaError as error:
                raise ConflictoSincronizacionError(
                    f"Central confirmó más eventos de los que tiene esta caja: {error}"
                ) from error
            confirmado = respuesta.ultimo_consecutivo_confirmado

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


def _rechazar_si_no_acepta_la_llave(respuesta: httpx.Response) -> None:
    if respuesta.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        raise CredencialRechazadaError(
            f"Central rechazó la llave de esta sucursal: {_detalle(respuesta)} "
            "Revisa LLAVE_CENTRAL en el .env de la tienda."
        )


def _detalle(respuesta: httpx.Response) -> str:
    try:
        return str(respuesta.json().get("detail", respuesta.text))
    except ValueError:
        return respuesta.text

"""API de los servicios centrales de Pumaverso POS."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request, status
from pumaverso_contratos import PATRON_IDENTIFICADOR, EventoContrato
from pydantic import BaseModel, Field

from pumaverso_central.cola import (
    ConflictoHashEventoError,
    eventos_fallidos,
    procesar_eventos_pendientes_cola,
    recibir_lote_eventos,
)
from pumaverso_central.config import CentralSettings, get_central_settings
from pumaverso_central.db import abrir_conexion_central, inicializar_esquema_central
from pumaverso_central.fantasma import (
    SospechaNoEncontradaError,
    detectar_inventario_fantasma,
    listar_sospechas,
    registrar_conteo,
)
from pumaverso_central.trabajador import TrabajadorCola

settings = get_central_settings()
registro = logging.getLogger(__name__)


def _detectar_fantasma_una_vez(configuracion: CentralSettings) -> None:
    with abrir_conexion_central(configuracion.database_url) as conn:
        resultado = detectar_inventario_fantasma(
            conn, parametros=configuracion.parametros_fantasma()
        )
    if resultado.nuevas or resultado.escaladas:
        registro.warning(
            "Inventario fantasma: %d sospechas nuevas, %d escaladas.",
            len(resultado.nuevas),
            resultado.escaladas,
        )


async def _detectar_fantasma_siempre(
    configuracion: CentralSettings, detener: asyncio.Event
) -> None:
    """Revisa las franjas terminadas cada ``fantasma_intervalo_segundos`` (decisión 0009)."""
    while not detener.is_set():
        try:
            await asyncio.to_thread(_detectar_fantasma_una_vez, configuracion)
        except Exception as error:  # noqa: BLE001 - un fallo no debe detener la detección
            registro.warning("No se pudo revisar inventario fantasma: %s", error)
        try:
            await asyncio.wait_for(
                detener.wait(), timeout=configuracion.fantasma_intervalo_segundos
            )
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    """Crea esquemas y tablas en PostgreSQL y arranca el procesamiento de la cola."""
    configuracion = get_central_settings()
    conn = abrir_conexion_central(configuracion.database_url)
    try:
        inicializar_esquema_central(conn)
    finally:
        conn.close()

    app_.state.trabajador_cola = None
    tareas: list[asyncio.Task[None]] = []
    detener = asyncio.Event()
    if configuracion.cola_automatica:
        trabajador = TrabajadorCola(
            abrir_conexion=lambda: abrir_conexion_central(configuracion.database_url),
            lote=configuracion.cola_lote,
            intervalo_segundos=configuracion.cola_intervalo_segundos,
        )
        app_.state.trabajador_cola = trabajador
        tareas.append(asyncio.create_task(trabajador.ejecutar_siempre(detener)))
    if configuracion.fantasma_automatico:
        tareas.append(asyncio.create_task(_detectar_fantasma_siempre(configuracion, detener)))

    yield

    detener.set()
    for tarea in tareas:
        with contextlib.suppress(asyncio.CancelledError):
            await tarea


def obtener_conexion_central() -> Iterator[psycopg.Connection]:
    """Abre y cierra una conexión PostgreSQL por solicitud HTTP."""
    conn = abrir_conexion_central(get_central_settings().database_url)
    try:
        yield conn
    finally:
        conn.close()


ConexionCentralDep = Annotated[psycopg.Connection, Depends(obtener_conexion_central)]

app = FastAPI(
    title=settings.app_name,
    description="API central de sincronización, inventario, usuarios y catálogo.",
    version=settings.app_version,
    lifespan=lifespan,
)


class LoteEventosRequest(BaseModel):
    sucursal_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    caja_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    eventos: list[EventoContrato] = Field(min_length=1)


class LoteEventosResponse(BaseModel):
    ultimo_consecutivo_confirmado: int
    hueco_detectado_desde: int | None = None


class UsuarioCentralPayload(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    nombre: str = Field(min_length=1, max_length=120)
    rol: str = Field(min_length=1, max_length=40)
    hash_pin: str = Field(min_length=1)
    activo: bool = True


class ConteoFisicoPayload(BaseModel):
    cantidad_contada: int = Field(ge=0)
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)


class ProductoCentralPayload(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    nombre: str = Field(min_length=1, max_length=160)
    precio_centavos: int = Field(ge=0)
    costo_centavos: int = Field(ge=0)
    activo: bool = True


@app.get("/health", tags=["Salud"])
def health_check() -> dict[str, str]:
    """Verifica que el servidor central esté operativo."""
    return {"status": "ok", "service": "pumaverso-central"}


@app.post("/sincronizacion/eventos", response_model=LoteEventosResponse, tags=["Sincronización"])
def subir_eventos_sucursal(
    datos: LoteEventosRequest, conn: ConexionCentralDep
) -> LoteEventosResponse:
    try:
        resultado = recibir_lote_eventos(
            conn,
            sucursal_id=datos.sucursal_id,
            caja_id=datos.caja_id,
            eventos=datos.eventos,
        )
    except ConflictoHashEventoError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return LoteEventosResponse(
        ultimo_consecutivo_confirmado=resultado.ultimo_consecutivo_confirmado,
        hueco_detectado_desde=resultado.hueco_detectado_desde,
    )


@app.get("/sincronizacion/actualizaciones", tags=["Sincronización"])
def obtener_actualizaciones(
    sucursal_id: str,
    conn: ConexionCentralDep,
    desde: datetime | None = None,
) -> dict[str, list[dict[str, object]]]:
    _ = sucursal_id
    if desde is None:
        usuarios = conn.execute(
            """
            SELECT usuario_id, nombre, rol, hash_pin, activo, actualizado_en
            FROM usuarios.usuarios
            """
        ).fetchall()
        productos = conn.execute(
            """
            SELECT sku, nombre, precio_centavos, precio_actualizado_en,
                   costo_centavos, costo_actualizado_en, activo
            FROM catalogo.productos
            """
        ).fetchall()
    else:
        usuarios = conn.execute(
            """
            SELECT usuario_id, nombre, rol, hash_pin, activo, actualizado_en
            FROM usuarios.usuarios
            WHERE actualizado_en >= %s
            """,
            (desde,),
        ).fetchall()
        productos = conn.execute(
            """
            SELECT sku, nombre, precio_centavos, precio_actualizado_en,
                   costo_centavos, costo_actualizado_en, activo
            FROM catalogo.productos
            WHERE precio_actualizado_en >= %s OR costo_actualizado_en >= %s
            """,
            (desde, desde),
        ).fetchall()

    return {
        "usuarios": [{**u, "actualizado_en": u["actualizado_en"].isoformat()} for u in usuarios],
        "productos": [
            {
                **p,
                "precio_actualizado_en": p["precio_actualizado_en"].isoformat(),
                "costo_actualizado_en": p["costo_actualizado_en"].isoformat(),
            }
            for p in productos
        ],
    }


@app.post("/sincronizacion/procesar-cola", tags=["Sincronización"])
def ejecutar_trabajador_cola(conn: ConexionCentralDep, limite: int = 50) -> dict[str, int]:
    resultado = procesar_eventos_pendientes_cola(conn, limite=limite)
    return {"procesados": resultado.procesados, "fallidos": resultado.fallidos}


@app.get("/sincronizacion/cola/trabajador", tags=["Sincronización"])
def consultar_trabajador_cola(request: Request) -> dict[str, object]:
    """Cómo va el procesamiento automático de la cola."""
    trabajador = getattr(request.app.state, "trabajador_cola", None)
    if trabajador is None:
        return {"automatico": False}
    estado = trabajador.estado
    return {
        "automatico": True,
        "ultimo_ciclo_en": estado.ultimo_ciclo_en.isoformat() if estado.ultimo_ciclo_en else None,
        "procesados_total": estado.procesados_total,
        "fallidos_total": estado.fallidos_total,
        "fallos_consecutivos": estado.fallos_consecutivos,
        "ultimo_error": estado.ultimo_error,
    }


@app.get("/sincronizacion/cola/fallidos", tags=["Sincronización"])
def consultar_eventos_fallidos(
    conn: ConexionCentralDep, limite: int = 100
) -> list[dict[str, object]]:
    """Eventos que no se pudieron aplicar y esperan revisión."""
    return [
        {**fila, "fallido_en": fila["fallido_en"].isoformat()}
        for fila in eventos_fallidos(conn, limite=limite)
    ]


@app.put("/usuarios/{usuario_id}", tags=["Usuarios"])
def guardar_usuario_central(
    usuario_id: str, datos: UsuarioCentralPayload, conn: ConexionCentralDep
) -> dict[str, str]:
    if usuario_id != datos.usuario_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario_id de la ruta no coincide con el cuerpo.",
        )
    ahora = datetime.now(UTC)
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO usuarios.usuarios (
                usuario_id, nombre, rol, hash_pin, activo, actualizado_en
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (usuario_id) DO UPDATE SET
                nombre = excluded.nombre,
                rol = excluded.rol,
                hash_pin = excluded.hash_pin,
                activo = excluded.activo,
                actualizado_en = excluded.actualizado_en
            """,
            (datos.usuario_id, datos.nombre, datos.rol, datos.hash_pin, datos.activo, ahora),
        )
    return {"status": "ok", "usuario_id": usuario_id}


@app.put("/catalogo/productos/{sku}", tags=["Catálogo"])
def guardar_producto_central(
    sku: str, datos: ProductoCentralPayload, conn: ConexionCentralDep
) -> dict[str, str]:
    if sku != datos.sku:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El SKU de la ruta no coincide con el cuerpo.",
        )
    ahora = datetime.now(UTC)
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO catalogo.productos (
                sku, nombre, precio_centavos, precio_actualizado_en,
                costo_centavos, costo_actualizado_en, activo
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (sku) DO UPDATE SET
                nombre = excluded.nombre,
                precio_centavos = excluded.precio_centavos,
                precio_actualizado_en = excluded.precio_actualizado_en,
                costo_centavos = excluded.costo_centavos,
                costo_actualizado_en = excluded.costo_actualizado_en,
                activo = excluded.activo
            """,
            (
                sku,
                datos.nombre,
                datos.precio_centavos,
                ahora,
                datos.costo_centavos,
                ahora,
                datos.activo,
            ),
        )
    return {"status": "ok", "sku": sku}


@app.post("/inventario/fantasma/detectar", tags=["Inventario fantasma"])
def detectar_fantasma(
    conn: ConexionCentralDep, momento: datetime | None = None
) -> dict[str, object]:
    """Revisa ahora las franjas terminadas de las últimas 24 horas.

    Central ya lo hace solo cada ``FANTASMA_INTERVALO_SEGUNDOS``. ``momento``
    permite revisar como si fuera otra hora (demos y pruebas).
    """
    if momento is not None and momento.tzinfo is None:
        raise HTTPException(status_code=422, detail="momento debe incluir zona horaria.")
    resultado = detectar_inventario_fantasma(
        conn, parametros=get_central_settings().parametros_fantasma(), ahora=momento
    )
    return {
        "franjas_evaluadas": resultado.franjas_evaluadas,
        "franjas_pendientes": resultado.franjas_pendientes,
        "sospechas_nuevas": resultado.nuevas,
        "sospechas_escaladas": resultado.escaladas,
    }


@app.get("/inventario/fantasma/sospechas", tags=["Inventario fantasma"])
def reporte_fantasma(
    conn: ConexionCentralDep,
    sucursal_id: str | None = None,
    incluir_resueltas: bool = False,
) -> dict[str, object]:
    """Reporte para el gerente: qué contar, qué pedir y cuánto se está dejando de vender."""
    sospechas = listar_sospechas(conn, sucursal_id=sucursal_id, solo_abiertas=not incluir_resueltas)
    abiertas = [s for s in sospechas if s["estado"] != "resuelta"]
    return {
        "sospechas": sospechas,
        "sugerencia_pedido": sorted({s["sku"] for s in abiertas}),
        "venta_perdida_estimada_centavos": sum(
            s["venta_perdida_estimada_centavos"] for s in abiertas
        ),
    }


@app.post("/inventario/fantasma/sospechas/{sospecha_id}/conteo", tags=["Inventario fantasma"])
def registrar_conteo_fisico(
    sospecha_id: int, datos: ConteoFisicoPayload, conn: ConexionCentralDep
) -> dict[str, object]:
    """Cierra la sospecha con el conteo del gerente y dice cuánto ajustar en la tienda."""
    try:
        return registrar_conteo(
            conn,
            sospecha_id=sospecha_id,
            cantidad_contada=datos.cantidad_contada,
            usuario_id=datos.usuario_id,
        )
    except SospechaNoEncontradaError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

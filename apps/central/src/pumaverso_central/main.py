"""API de los servicios centrales de Pumaverso POS."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

import psycopg
from fastapi import Depends, FastAPI, HTTPException, status
from pumaverso_contratos import PATRON_IDENTIFICADOR, EventoContrato
from pydantic import BaseModel, Field

from pumaverso_central.cola import (
    ConflictoHashEventoError,
    procesar_eventos_pendientes_cola,
    recibir_lote_eventos,
)
from pumaverso_central.config import get_central_settings
from pumaverso_central.db import abrir_conexion_central, inicializar_esquema_central

settings = get_central_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Crea esquemas y tablas en PostgreSQL al iniciar el servidor central."""
    conn = abrir_conexion_central(get_central_settings().database_url)
    try:
        inicializar_esquema_central(conn)
    finally:
        conn.close()
    yield


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
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error

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
        "usuarios": [
            {**u, "actualizado_en": u["actualizado_en"].isoformat()} for u in usuarios
        ],
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
    procesados = procesar_eventos_pendientes_cola(conn, limite=limite)
    return {"procesados": procesados}


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
            (sku, datos.nombre, datos.precio_centavos, ahora, datos.costo_centavos, ahora, datos.activo),
        )
    return {"status": "ok", "sku": sku}
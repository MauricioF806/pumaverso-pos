"""Endpoints para consultar estado y disparar sincronización con central."""

import sqlite3
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from pumaverso_tienda.api.dependencias import obtener_conexion, obtener_transporte_central
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.sincronizacion import (
    ConflictoSincronizacionError,
    ErrorSincronizacion,
    TransporteCentral,
    estado_sincronizacion_caja,
    sincronizar_nodo,
)

router = APIRouter(prefix="/sincronizacion", tags=["Sincronización"])
ConexionDep = Annotated[sqlite3.Connection, Depends(obtener_conexion)]
TransporteDep = Annotated[TransporteCentral, Depends(obtener_transporte_central)]


class EstadoTrabajadorResponse(BaseModel):
    ultimo_intento_en: datetime | None
    ultimo_exito_en: datetime | None
    fallos_consecutivos: int
    en_conflicto: bool
    ultimo_error: str | None
    proximo_intento_en: datetime | None


class EstadoSyncResponse(BaseModel):
    sucursal_id: str
    caja_id: str
    ultimo_consecutivo: int
    ultimo_consecutivo_confirmado: int
    pendientes: int
    sincronizacion_automatica: bool
    trabajador: EstadoTrabajadorResponse | None = None


class ResultadoSyncResponse(BaseModel):
    eventos_enviados: int
    ultimo_consecutivo_confirmado: int
    usuarios_actualizados: int
    productos_actualizados: int
    hueco_detectado_desde: int | None = None


@router.get("/estado", response_model=EstadoSyncResponse)
def consultar_estado_sync(request: Request, conexion: ConexionDep) -> EstadoSyncResponse:
    """Eventos pendientes y, si está activa, cómo va la sincronización automática."""
    settings = get_settings()
    estado = estado_sincronizacion_caja(conexion, settings.sucursal_id, settings.caja_id)
    trabajador = getattr(request.app.state, "trabajador_sync", None)
    return EstadoSyncResponse(
        sucursal_id=settings.sucursal_id,
        caja_id=settings.caja_id,
        **estado,
        sincronizacion_automatica=trabajador is not None,
        trabajador=(
            EstadoTrabajadorResponse(**_campos_estado(trabajador.estado)) if trabajador else None
        ),
    )


def _campos_estado(estado) -> dict[str, object]:
    return {campo: getattr(estado, campo) for campo in EstadoTrabajadorResponse.model_fields}


@router.post("/ejecutar", response_model=ResultadoSyncResponse)
def ejecutar_sync(conexion: ConexionDep, transporte: TransporteDep) -> ResultadoSyncResponse:
    settings = get_settings()
    try:
        resultado = sincronizar_nodo(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            transporte=transporte,
            limite_lote=settings.sync_lote_maximo,
        )
    except ConflictoSincronizacionError as error:
        # La bajada (usuarios y precios) sí se aplicó; solo la subida quedó detenida.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{error} Los usuarios y precios sí se actualizaron. Requiere revisión.",
        ) from error
    except ErrorSincronizacion as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error

    return ResultadoSyncResponse(
        eventos_enviados=resultado.eventos_enviados,
        ultimo_consecutivo_confirmado=resultado.ultimo_consecutivo_confirmado,
        usuarios_actualizados=resultado.usuarios_actualizados,
        productos_actualizados=resultado.productos_actualizados,
        hueco_detectado_desde=resultado.hueco_detectado_desde,
    )

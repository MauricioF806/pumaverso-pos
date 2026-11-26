"""Endpoints para consultar estado y disparar sincronización con central."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from pumaverso_tienda.api.dependencias import obtener_conexion, obtener_transporte_central
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.sincronizacion import (
    ErrorSincronizacion,
    TransporteCentral,
    estado_sincronizacion_caja,
    sincronizar_nodo,
)

router = APIRouter(prefix="/sincronizacion", tags=["Sincronización"])
ConexionDep = Annotated[sqlite3.Connection, Depends(obtener_conexion)]
TransporteDep = Annotated[TransporteCentral, Depends(obtener_transporte_central)]


class EstadoSyncResponse(BaseModel):
    sucursal_id: str
    caja_id: str
    ultimo_consecutivo: int
    ultimo_consecutivo_confirmado: int
    pendientes: int


class ResultadoSyncResponse(BaseModel):
    eventos_enviados: int
    ultimo_consecutivo_confirmado: int
    usuarios_actualizados: int
    productos_actualizados: int
    hueco_detectado_desde: int | None = None


@router.get("/estado", response_model=EstadoSyncResponse)
def consultar_estado_sync(conexion: ConexionDep) -> EstadoSyncResponse:
    settings = get_settings()
    estado = estado_sincronizacion_caja(conexion, settings.sucursal_id, settings.caja_id)
    return EstadoSyncResponse(
        sucursal_id=settings.sucursal_id,
        caja_id=settings.caja_id,
        **estado,
    )


@router.post("/ejecutar", response_model=ResultadoSyncResponse)
def ejecutar_sync(
    conexion: ConexionDep, transporte: TransporteDep
) -> ResultadoSyncResponse:
    settings = get_settings()
    try:
        resultado = sincronizar_nodo(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            transporte=transporte,
            limite_lote=settings.sync_lote_maximo,
        )
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
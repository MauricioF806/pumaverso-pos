"""Reporte de ventas para el gerente (decisión 0012). Funciona sin conexión."""

import sqlite3
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from pumaverso_tienda.api.dependencias import obtener_conexion, requerir_gerente
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.pos import reporte_de_ventas
from pumaverso_tienda.usuarios import UsuarioLocal

router = APIRouter(prefix="/reportes", tags=["Reportes"])


class CajeroHttp(BaseModel):
    usuario_id: str
    nombre: str
    ventas: int
    total_centavos: int


class ProductoHttp(BaseModel):
    sku: str
    nombre: str
    piezas: int
    total_centavos: int


class ReporteVentasHttp(BaseModel):
    desde: datetime
    hasta: datetime
    ventas: int
    total_centavos: int
    piezas: int
    ticket_promedio_centavos: int
    canceladas: int
    canceladas_centavos: int
    con_inventario_negativo: int
    por_cajero: list[CajeroHttp]
    productos_mas_vendidos: list[ProductoHttp]


@router.get("/ventas", response_model=ReporteVentasHttp)
def ventas(
    desde: datetime,
    hasta: datetime,
    conexion: Annotated[sqlite3.Connection, Depends(obtener_conexion)],
    _gerente: Annotated[UsuarioLocal, Depends(requerir_gerente)],
) -> ReporteVentasHttp:
    """Ventas de la caja entre ``desde`` y ``hasta`` (con zona horaria, máximo 31 días).

    Para "hoy", la pantalla manda la medianoche local y la medianoche siguiente.
    """
    settings = get_settings()
    try:
        reporte = reporte_de_ventas(
            conexion, settings.sucursal_id, settings.caja_id, desde=desde, hasta=hasta
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ReporteVentasHttp.model_validate(reporte, from_attributes=True)

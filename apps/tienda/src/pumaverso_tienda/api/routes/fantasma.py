"""Inventario fantasma para el gerente de la sucursal (decisiones 0009 y 0010).

El reporte vive en central, que es quien ve las ventas de todas las franjas.
La tienda lo pide con la llave de su sucursal, así que solo recibe lo suyo, y le
agrega la existencia local para que el gerente sepa cuánto ajustar.

Necesita conexión con central. Si no hay, se explica; vender no depende de esto.
"""

import sqlite3
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from pumaverso_tienda.api.dependencias import (
    obtener_conexion,
    obtener_transporte_central,
    obtener_usuario_actual,
)
from pumaverso_tienda.catalogo import obtener_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.inventario import obtener_existencia
from pumaverso_tienda.sincronizacion import (
    CredencialRechazadaError,
    ErrorSincronizacion,
    SospechaNoEncontradaError,
)
from pumaverso_tienda.usuarios import ROLES_APROBADORES, UsuarioLocal

router = APIRouter(prefix="/inventario/fantasma", tags=["Inventario fantasma"])


def requerir_gerente(
    usuario: Annotated[UsuarioLocal, Depends(obtener_usuario_actual)],
) -> UsuarioLocal:
    if usuario.rol not in ROLES_APROBADORES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El reporte de inventario fantasma es para el gerente o el supervisor.",
        )
    return usuario


ConexionDep = Annotated[sqlite3.Connection, Depends(obtener_conexion)]
GerenteDep = Annotated[UsuarioLocal, Depends(requerir_gerente)]
TransporteDep = Annotated[object, Depends(obtener_transporte_central)]


class SospechaHttp(BaseModel):
    id: int
    sku: str
    nombre: str
    estado: str
    franja_inicio: datetime
    franja_fin: datetime
    unidades_esperadas: float
    unidades_vendidas: int
    venta_perdida_estimada_centavos: int
    detectada_en: datetime
    escalada_en: datetime | None
    existencia_local: int


# Lo que se copia tal cual del reporte de central; nombre y existencia los pone la tienda.
CAMPOS_DE_CENTRAL = tuple(
    c for c in SospechaHttp.model_fields if c not in {"nombre", "existencia_local"}
)


class ReporteFantasmaHttp(BaseModel):
    sospechas: list[SospechaHttp]
    venta_perdida_estimada_centavos: int


class SolicitudConteo(BaseModel):
    cantidad_contada: int = Field(ge=0)


class RespuestaConteo(BaseModel):
    sospecha_id: int
    sku: str
    cantidad_contada: int
    existencia_local: int
    diferencia: int


def _pedir_a_central(funcion, *args):
    try:
        return funcion(*args)
    except CredencialRechazadaError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error
    except SospechaNoEncontradaError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esa sospecha ya no está abierta: quizá alguien ya registró el conteo.",
        ) from error
    except ErrorSincronizacion as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Sin conexión con central. El reporte de inventario fantasma vive en "
                "central; intenta de nuevo cuando vuelva la conexión."
            ),
        ) from error


@router.get("", response_model=ReporteFantasmaHttp)
def reporte_fantasma(
    conexion: ConexionDep, _gerente: GerenteDep, transporte: TransporteDep
) -> ReporteFantasmaHttp:
    """Sospechas abiertas de esta sucursal, con la existencia que tiene la tienda."""
    datos = _pedir_a_central(transporte.consultar_sospechas_fantasma, get_settings().sucursal_id)
    sospechas = []
    for s in datos["sospechas"]:
        producto = obtener_producto(conexion, s["sku"])
        sospechas.append(
            SospechaHttp(
                **{campo: s[campo] for campo in CAMPOS_DE_CENTRAL},
                nombre=s.get("nombre") or (producto.nombre if producto else s["sku"]),
                existencia_local=obtener_existencia(conexion, s["sku"]),
            )
        )
    return ReporteFantasmaHttp(
        sospechas=sospechas,
        venta_perdida_estimada_centavos=datos["venta_perdida_estimada_centavos"],
    )


@router.post("/{sospecha_id}/conteo", response_model=RespuestaConteo)
def registrar_conteo(
    sospecha_id: int,
    datos: SolicitudConteo,
    conexion: ConexionDep,
    gerente: GerenteDep,
    transporte: TransporteDep,
) -> RespuestaConteo:
    """Cierra la sospecha en central y dice cuánto ajustar contra la existencia local.

    La diferencia se calcula contra lo que tiene la tienda, no central: la tienda
    es la dueña de su inventario y puede tener ventas que central aún no recibe.
    """
    cerrada = _pedir_a_central(
        transporte.registrar_conteo_fantasma,
        sospecha_id,
        datos.cantidad_contada,
        gerente.usuario_id,
    )
    existencia = obtener_existencia(conexion, cerrada["sku"])
    return RespuestaConteo(
        sospecha_id=sospecha_id,
        sku=cerrada["sku"],
        cantidad_contada=datos.cantidad_contada,
        existencia_local=existencia,
        diferencia=datos.cantidad_contada - existencia,
    )

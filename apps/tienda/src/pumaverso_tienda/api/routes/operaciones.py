"""Endpoints locales para sesión, catálogo, inventario y ventas POS.

Todas las operaciones requieren una sesión (``POST /auth/login``) y se registran
a nombre del usuario de la sesión. Cancelaciones y ajustes requieren además el
usuario y PIN de un gerente o supervisor distinto de quien opera (decisión 0006).
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pumaverso_contratos import PATRON_IDENTIFICADOR, LineaRecepcion
from pydantic import BaseModel, Field

from pumaverso_tienda.api.dependencias import (
    obtener_conexion,
    obtener_token,
    obtener_usuario_actual,
)
from pumaverso_tienda.catalogo import (
    PrecioExpiradoError,
    ProductoInactivoError,
    ProductoNoEncontradoError,
    validar_producto_para_venta,
)
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.inventario import (
    obtener_existencia,
    registrar_ajuste_inventario,
    registrar_merma,
    registrar_recepcion_proveedor,
)
from pumaverso_tienda.pos import (
    ItemVentaSolicitado,
    RelojAtrasadoError,
    VentaNoCancelableError,
    cancelar_venta,
    registrar_venta,
)
from pumaverso_tienda.usuarios import (
    AprobacionInvalidaError,
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    UsuarioLocal,
    autenticar_usuario,
    cerrar_sesion,
    iniciar_sesion,
)

router = APIRouter(tags=["Operación local"])
ConexionDep = Annotated[sqlite3.Connection, Depends(obtener_conexion)]
UsuarioActualDep = Annotated[UsuarioLocal, Depends(obtener_usuario_actual)]


class SolicitudLogin(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    pin: str = Field(min_length=1, max_length=32)


class RespuestaLogin(BaseModel):
    token: str
    expira_en: datetime
    usuario_id: str
    nombre: str
    rol: str


class RespuestaProducto(BaseModel):
    sku: str
    nombre: str
    precio_centavos: int
    existencia: int
    costo_requiere_revision: bool


class ItemVentaBody(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)


class SolicitudVenta(BaseModel):
    items: list[ItemVentaBody] = Field(min_length=1)


class RespuestaVentaHttp(BaseModel):
    sucursal_id: str
    caja_id: str
    consecutivo: int
    total_centavos: int
    hash: str
    inventario_negativo: bool
    alerta_inventario_negativo: bool
    skus_costo_por_revisar: list[str]


class Aprobacion(BaseModel):
    """Gerente o supervisor que autoriza la operación, con su propio PIN."""

    aprobador_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    aprobador_pin: str = Field(min_length=1, max_length=32)


class SolicitudCancelacion(Aprobacion):
    motivo: str = Field(min_length=1, max_length=255)


class SolicitudRecepcion(BaseModel):
    proveedor_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    folio_documento: str = Field(min_length=1, max_length=64)
    partidas: list[LineaRecepcion] = Field(min_length=1)


class SolicitudMerma(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)


class SolicitudAjuste(Aprobacion):
    sku: str = Field(min_length=1, max_length=64)
    diferencia_cantidad: int
    motivo: str = Field(min_length=1, max_length=255)


class RespuestaEventoCreado(BaseModel):
    sucursal_id: str
    caja_id: str
    consecutivo: int
    tipo: str
    hash: str


@contextmanager
def _traducir_errores() -> Iterator[None]:
    """Convierte los errores del dominio en respuestas HTTP."""
    try:
        yield
    except CredencialesInvalidasError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (UsuarioInactivoError, CredencialesExpiradasError, AprobacionInvalidaError) as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ProductoNoEncontradoError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=error.args[0]) from error
    except (
        ProductoInactivoError,
        PrecioExpiradoError,
        RelojAtrasadoError,
        VentaNoCancelableError,
    ) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error


def _verificar_pin_aprobador(conexion: sqlite3.Connection, aprobacion: Aprobacion) -> str:
    """Comprueba el PIN del aprobador; que pueda aprobar lo valida el dominio."""
    try:
        autenticar_usuario(conexion, aprobacion.aprobador_id, aprobacion.aprobador_pin)
    except (CredencialesInvalidasError, UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail=f"Aprobación rechazada: {error}"
        ) from error
    return aprobacion.aprobador_id


def _evento_creado(evento) -> RespuestaEventoCreado:
    return RespuestaEventoCreado(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo.value,
        hash=evento.hash,
    )


@router.post("/auth/login", response_model=RespuestaLogin)
def login_local(datos: SolicitudLogin, conexion: ConexionDep) -> RespuestaLogin:
    """Valida usuario y PIN y entrega el token de la sesión."""
    with _traducir_errores():
        sesion = iniciar_sesion(conexion, datos.usuario_id, datos.pin)
    return RespuestaLogin(
        token=sesion.token,
        expira_en=sesion.expira_en,
        usuario_id=sesion.usuario.usuario_id,
        nombre=sesion.usuario.nombre,
        rol=sesion.usuario.rol,
    )


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout_local(conexion: ConexionDep, token: Annotated[str, Depends(obtener_token)]) -> Response:
    """Cierra la sesión del token enviado."""
    cerrar_sesion(conexion, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/catalogo/{sku}", response_model=RespuestaProducto)
def consultar_producto(sku: str, conexion: ConexionDep) -> RespuestaProducto:
    with _traducir_errores():
        validacion = validar_producto_para_venta(conexion, sku)

    producto = validacion.producto
    return RespuestaProducto(
        sku=producto.sku,
        nombre=producto.nombre,
        precio_centavos=producto.precio_centavos,
        existencia=obtener_existencia(conexion, sku),
        costo_requiere_revision=validacion.costo_requiere_revision,
    )


@router.post("/pos/ventas", response_model=RespuestaVentaHttp, status_code=status.HTTP_201_CREATED)
def crear_venta(
    datos: SolicitudVenta, conexion: ConexionDep, usuario: UsuarioActualDep
) -> RespuestaVentaHttp:
    settings = get_settings()
    with _traducir_errores():
        resultado = registrar_venta(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=usuario.usuario_id,
            items=[ItemVentaSolicitado(sku=i.sku, cantidad=i.cantidad) for i in datos.items],
        )

    evento = resultado.evento
    return RespuestaVentaHttp(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        total_centavos=resultado.total_centavos,
        hash=evento.hash,
        inventario_negativo=resultado.inventario_negativo,
        alerta_inventario_negativo=resultado.alerta_inventario_negativo,
        skus_costo_por_revisar=list(resultado.skus_costo_por_revisar),
    )


@router.post(
    "/pos/ventas/{consecutivo}/cancelar",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def cancelar_venta_endpoint(
    consecutivo: int,
    datos: SolicitudCancelacion,
    conexion: ConexionDep,
    usuario: UsuarioActualDep,
) -> RespuestaEventoCreado:
    settings = get_settings()
    aprobador_id = _verificar_pin_aprobador(conexion, datos)
    with _traducir_errores():
        evento = cancelar_venta(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=usuario.usuario_id,
            consecutivo_original=consecutivo,
            motivo=datos.motivo,
            aprobado_por=aprobador_id,
        )
    return _evento_creado(evento)


@router.post(
    "/inventario/recepciones",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_recepcion(
    datos: SolicitudRecepcion, conexion: ConexionDep, usuario: UsuarioActualDep
) -> RespuestaEventoCreado:
    settings = get_settings()
    with _traducir_errores():
        evento = registrar_recepcion_proveedor(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=usuario.usuario_id,
            proveedor_id=datos.proveedor_id,
            folio_documento=datos.folio_documento,
            partidas=datos.partidas,
        )
    return _evento_creado(evento)


@router.post(
    "/inventario/mermas",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_merma(
    datos: SolicitudMerma, conexion: ConexionDep, usuario: UsuarioActualDep
) -> RespuestaEventoCreado:
    settings = get_settings()
    with _traducir_errores():
        evento = registrar_merma(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=usuario.usuario_id,
            sku=datos.sku,
            cantidad=datos.cantidad,
            motivo=datos.motivo,
        )
    return _evento_creado(evento)


@router.post(
    "/inventario/ajustes",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_ajuste(
    datos: SolicitudAjuste, conexion: ConexionDep, usuario: UsuarioActualDep
) -> RespuestaEventoCreado:
    settings = get_settings()
    aprobador_id = _verificar_pin_aprobador(conexion, datos)
    with _traducir_errores():
        try:
            evento = registrar_ajuste_inventario(
                conexion,
                sucursal_id=settings.sucursal_id,
                caja_id=settings.caja_id,
                usuario_id=usuario.usuario_id,
                sku=datos.sku,
                diferencia_cantidad=datos.diferencia_cantidad,
                motivo=datos.motivo,
                aprobado_por=aprobador_id,
            )
        except ValueError as error:
            raise HTTPException(422, detail=str(error)) from error
    return _evento_creado(evento)

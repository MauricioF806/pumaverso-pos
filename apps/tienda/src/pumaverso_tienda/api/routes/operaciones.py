"""Endpoints locales para autenticación, catálogo, inventario y ventas POS."""

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pumaverso_contratos import PATRON_IDENTIFICADOR, LineaRecepcion
from pydantic import BaseModel, Field

from pumaverso_tienda.api.dependencias import obtener_conexion
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
    CredencialesExpiradasError,
    CredencialesInvalidasError,
    UsuarioInactivoError,
    autenticar_usuario,
)

router = APIRouter(tags=["Operación local"])
ConexionDep = Annotated[sqlite3.Connection, Depends(obtener_conexion)]


class SolicitudLogin(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    pin: str = Field(min_length=1, max_length=32)


class RespuestaUsuario(BaseModel):
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
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
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


class SolicitudCancelacion(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    motivo: str = Field(min_length=1, max_length=255)
    aprobado_por: str = Field(pattern=PATRON_IDENTIFICADOR)


class SolicitudRecepcion(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    proveedor_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    folio_documento: str = Field(min_length=1, max_length=64)
    partidas: list[LineaRecepcion] = Field(min_length=1)


class SolicitudMerma(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    sku: str = Field(min_length=1, max_length=64)
    cantidad: int = Field(gt=0)
    motivo: str = Field(min_length=1, max_length=255)


class SolicitudAjuste(BaseModel):
    usuario_id: str = Field(pattern=PATRON_IDENTIFICADOR)
    sku: str = Field(min_length=1, max_length=64)
    diferencia_cantidad: int
    motivo: str = Field(min_length=1, max_length=255)
    aprobado_por: str = Field(pattern=PATRON_IDENTIFICADOR)


class RespuestaEventoCreado(BaseModel):
    sucursal_id: str
    caja_id: str
    consecutivo: int
    tipo: str
    hash: str


@router.post("/auth/login", response_model=RespuestaUsuario)
def login_local(datos: SolicitudLogin, conexion: ConexionDep) -> RespuestaUsuario:
    try:
        usuario = autenticar_usuario(conexion, datos.usuario_id, datos.pin)
    except CredencialesInvalidasError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error

    return RespuestaUsuario(
        usuario_id=usuario.usuario_id,
        nombre=usuario.nombre,
        rol=usuario.rol,
    )


@router.get("/catalogo/{sku}", response_model=RespuestaProducto)
def consultar_producto(sku: str, conexion: ConexionDep) -> RespuestaProducto:
    try:
        validacion = validar_producto_para_venta(conexion, sku)
    except ProductoNoEncontradoError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ProductoInactivoError, PrecioExpiradoError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    producto = validacion.producto
    return RespuestaProducto(
        sku=producto.sku,
        nombre=producto.nombre,
        precio_centavos=producto.precio_centavos,
        existencia=obtener_existencia(conexion, sku),
        costo_requiere_revision=validacion.costo_requiere_revision,
    )


@router.post(
    "/pos/ventas",
    response_model=RespuestaVentaHttp,
    status_code=status.HTTP_201_CREATED,
)
def crear_venta(datos: SolicitudVenta, conexion: ConexionDep) -> RespuestaVentaHttp:
    settings = get_settings()
    try:
        resultado = registrar_venta(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=datos.usuario_id,
            items=[ItemVentaSolicitado(sku=i.sku, cantidad=i.cantidad) for i in datos.items],
        )
    except CredencialesInvalidasError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except ProductoNoEncontradoError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except (ProductoInactivoError, PrecioExpiradoError, RelojAtrasadoError) as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

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
    consecutivo: int, datos: SolicitudCancelacion, conexion: ConexionDep
) -> RespuestaEventoCreado:
    settings = get_settings()
    try:
        evento = cancelar_venta(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=datos.usuario_id,
            consecutivo_original=consecutivo,
            motivo=datos.motivo,
            aprobado_por=datos.aprobado_por,
        )
    except CredencialesInvalidasError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error
    except (UsuarioInactivoError, CredencialesExpiradasError) as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except VentaNoCancelableError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return RespuestaEventoCreado(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo.value,
        hash=evento.hash,
    )


@router.post(
    "/inventario/recepciones",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_recepcion(datos: SolicitudRecepcion, conexion: ConexionDep) -> RespuestaEventoCreado:
    settings = get_settings()
    try:
        evento = registrar_recepcion_proveedor(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=datos.usuario_id,
            proveedor_id=datos.proveedor_id,
            folio_documento=datos.folio_documento,
            partidas=datos.partidas,
        )
    except ProductoNoEncontradoError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return RespuestaEventoCreado(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo.value,
        hash=evento.hash,
    )


@router.post(
    "/inventario/mermas",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_merma(datos: SolicitudMerma, conexion: ConexionDep) -> RespuestaEventoCreado:
    settings = get_settings()
    try:
        evento = registrar_merma(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=datos.usuario_id,
            sku=datos.sku,
            cantidad=datos.cantidad,
            motivo=datos.motivo,
        )
    except ProductoNoEncontradoError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return RespuestaEventoCreado(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo.value,
        hash=evento.hash,
    )


@router.post(
    "/inventario/ajustes",
    response_model=RespuestaEventoCreado,
    status_code=status.HTTP_201_CREATED,
)
def crear_ajuste(datos: SolicitudAjuste, conexion: ConexionDep) -> RespuestaEventoCreado:
    settings = get_settings()
    try:
        evento = registrar_ajuste_inventario(
            conexion,
            sucursal_id=settings.sucursal_id,
            caja_id=settings.caja_id,
            usuario_id=datos.usuario_id,
            sku=datos.sku,
            diferencia_cantidad=datos.diferencia_cantidad,
            motivo=datos.motivo,
            aprobado_por=datos.aprobado_por,
        )
    except ProductoNoEncontradoError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error

    return RespuestaEventoCreado(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo.value,
        hash=evento.hash,
    )
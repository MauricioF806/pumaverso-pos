"""Contratos de datos compartidos entre la tienda y los servicios centrales."""

from pumaverso_contratos.eventos import (
    MODELOS_CONTENIDO,
    PATRON_IDENTIFICADOR,
    VERSION_ESQUEMA_ACTUAL,
    ContenidoAjuste,
    ContenidoCancelacion,
    ContenidoMerma,
    ContenidoRecepcionProveedor,
    ContenidoTraspaso,
    ContenidoVenta,
    EventoContrato,
    LineaProducto,
    LineaRecepcion,
    LineaTraspaso,
    TipoEvento,
    serializar_contenido_evento,
    validar_contenido_por_tipo,
)

__all__ = [
    "MODELOS_CONTENIDO",
    "PATRON_IDENTIFICADOR",
    "VERSION_ESQUEMA_ACTUAL",
    "ContenidoAjuste",
    "ContenidoCancelacion",
    "ContenidoMerma",
    "ContenidoRecepcionProveedor",
    "ContenidoTraspaso",
    "ContenidoVenta",
    "EventoContrato",
    "LineaProducto",
    "LineaRecepcion",
    "LineaTraspaso",
    "TipoEvento",
    "serializar_contenido_evento",
    "validar_contenido_por_tipo",
]
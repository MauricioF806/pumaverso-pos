"""Operaciones de punto de venta: cobro atómico y cancelaciones append-only."""

# RelojAtrasadoError vive en sincronizacion.reloj; se reexporta por compatibilidad.
from pumaverso_tienda.pos.servicio import (
    ItemVentaSolicitado,
    ResultadoVenta,
    VentaNoCancelableError,
    cancelar_venta,
    registrar_venta,
)
from pumaverso_tienda.sincronizacion.reloj import RelojAtrasadoError

__all__ = [
    "ItemVentaSolicitado",
    "RelojAtrasadoError",
    "ResultadoVenta",
    "VentaNoCancelableError",
    "cancelar_venta",
    "registrar_venta",
]

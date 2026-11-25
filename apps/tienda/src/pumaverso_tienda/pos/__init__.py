"""Operaciones de punto de venta: cobro atómico y cancelaciones append-only."""

from pumaverso_tienda.pos.servicio import (
    ItemVentaSolicitado,
    RelojAtrasadoError,
    ResultadoVenta,
    VentaNoCancelableError,
    cancelar_venta,
    registrar_venta,
)

__all__ = [
    "ItemVentaSolicitado",
    "RelojAtrasadoError",
    "ResultadoVenta",
    "VentaNoCancelableError",
    "cancelar_venta",
    "registrar_venta",
]
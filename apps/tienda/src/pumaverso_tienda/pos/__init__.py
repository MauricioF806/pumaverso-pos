"""Operaciones de punto de venta: cobro atómico y cancelaciones append-only."""

# RelojAtrasadoError vive en sincronizacion.reloj; se reexporta por compatibilidad.
from pumaverso_tienda.pos.consultas import (
    PartidaVenta,
    VentaRegistrada,
    listar_ventas_recientes,
    obtener_venta,
)
from pumaverso_tienda.pos.reportes import ReporteVentas, reporte_de_ventas
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
    "PartidaVenta",
    "RelojAtrasadoError",
    "ReporteVentas",
    "ResultadoVenta",
    "VentaNoCancelableError",
    "VentaRegistrada",
    "cancelar_venta",
    "listar_ventas_recientes",
    "obtener_venta",
    "registrar_venta",
    "reporte_de_ventas",
]

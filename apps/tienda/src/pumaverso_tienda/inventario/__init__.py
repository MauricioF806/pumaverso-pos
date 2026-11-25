"""Existencias locales y movimientos de inventario de la sucursal."""

from pumaverso_tienda.inventario.servicio import (
    ExistenciaProducto,
    ajustar_existencia_local,
    obtener_existencia,
    registrar_ajuste_inventario,
    registrar_merma,
    registrar_recepcion_proveedor,
)

__all__ = [
    "ExistenciaProducto",
    "ajustar_existencia_local",
    "obtener_existencia",
    "registrar_ajuste_inventario",
    "registrar_merma",
    "registrar_recepcion_proveedor",
]
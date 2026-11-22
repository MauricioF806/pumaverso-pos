"""Acceso a la base de datos SQLite de la sucursal: conexión, transacciones y migraciones."""

from pumaverso_tienda.persistencia.conexion import abrir_conexion
from pumaverso_tienda.persistencia.migraciones import (
    MigracionError,
    aplicar_migraciones,
    version_actual,
)
from pumaverso_tienda.persistencia.transacciones import TransaccionAnidadaError, transaccion

__all__ = [
    "MigracionError",
    "TransaccionAnidadaError",
    "abrir_conexion",
    "aplicar_migraciones",
    "transaccion",
    "version_actual",
]

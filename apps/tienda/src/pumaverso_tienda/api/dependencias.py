"""Dependencias compartidas para los endpoints FastAPI del nodo local."""

import sqlite3
from collections.abc import Iterator

from pumaverso_tienda.config import get_settings
from pumaverso_tienda.persistencia import abrir_conexion
from pumaverso_tienda.sincronizacion import TransporteCentral, TransporteCentralHttp


def obtener_conexion() -> Iterator[sqlite3.Connection]:
    """Abre y cierra una conexión SQLite por solicitud HTTP."""
    conexion = abrir_conexion(get_settings().database_path)
    try:
        yield conexion
    finally:
        conexion.close()


def obtener_transporte_central() -> TransporteCentral:
    """Devuelve el cliente HTTP configurado para hablar con central."""
    return TransporteCentralHttp(get_settings().central_url)
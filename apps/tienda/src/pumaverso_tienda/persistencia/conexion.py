"""Conexión a la base de datos SQLite de la sucursal."""

import sqlite3
from pathlib import Path

ESPERA_POR_BLOQUEO_MS = 5000


def abrir_conexion(ruta: Path | str) -> sqlite3.Connection:
    """Abre la base de datos local con la configuración que exige el PRD.

    - ``journal_mode = WAL`` y ``synchronous = FULL``: una transacción confirmada
      ya está en disco, así que sobrevive a un apagón.
    - ``autocommit=True``: Python no abre transacciones por su cuenta. Toda
      escritura que deba ser atómica se hace dentro de ``transaccion()``.
    - ``foreign_keys = ON``: SQLite no revisa llaves foráneas si no se activa.
    """
    if str(ruta) != ":memory:":
        Path(ruta).parent.mkdir(parents=True, exist_ok=True)

    conexion = sqlite3.connect(ruta, autocommit=True)
    conexion.row_factory = sqlite3.Row
    conexion.execute("PRAGMA journal_mode = WAL")
    conexion.execute("PRAGMA synchronous = FULL")
    conexion.execute("PRAGMA foreign_keys = ON")
    conexion.execute(f"PRAGMA busy_timeout = {ESPERA_POR_BLOQUEO_MS}")
    return conexion

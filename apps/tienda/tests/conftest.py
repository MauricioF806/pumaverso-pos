import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones


@pytest.fixture
def ruta_bd(tmp_path: Path) -> Path:
    """Ruta a un archivo de base de datos nuevo para cada prueba."""
    return tmp_path / "datos" / "pumaverso.db"


@pytest.fixture
def conexion(ruta_bd: Path) -> Iterator[sqlite3.Connection]:
    """Conexión a una base de datos con todas las migraciones aplicadas."""
    conexion = abrir_conexion(ruta_bd)
    aplicar_migraciones(conexion)
    yield conexion
    conexion.close()

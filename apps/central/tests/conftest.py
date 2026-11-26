from collections.abc import Iterator

import psycopg
import pytest

from pumaverso_central.config import get_central_settings
from pumaverso_central.db import abrir_conexion_central, inicializar_esquema_central


@pytest.fixture
def conn_central() -> Iterator[psycopg.Connection]:
    """Reinicia los esquemas de PostgreSQL antes de cada prueba."""
    url = get_central_settings().database_url
    conn = abrir_conexion_central(url)
    with conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS sincronizacion, usuarios, catalogo, inventario CASCADE")
    inicializar_esquema_central(conn)
    yield conn
    conn.close()
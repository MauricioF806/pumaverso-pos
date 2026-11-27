"""Las pruebas de central usan su propia base de datos de PostgreSQL.

Por defecto es ``pumaverso_central_test`` en el mismo servidor que levanta
``docker compose``; se crea sola si no existe. Se puede cambiar con la variable
``CENTRAL_TEST_DATABASE_URL``. Las pruebas se niegan a correr contra la base de
desarrollo, porque reinician los esquemas antes de cada prueba.
"""

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from pumaverso_central.config import get_central_settings
from pumaverso_central.db import abrir_conexion_central, inicializar_esquema_central

# Las pruebas procesan la cola a mano; las del trabajador lo activan a propósito.
os.environ["CENTRAL_COLA_AUTOMATICA"] = "false"

URL_PRUEBAS_POR_DEFECTO = "postgresql://pumaverso:pumaverso@127.0.0.1:5432/pumaverso_central_test"

# Si PostgreSQL no responde, no esperar indefinidamente antes de saltar las pruebas.
SEGUNDOS_ESPERA_CONEXION = 3


def _url_pruebas() -> str:
    url = os.environ.get("CENTRAL_TEST_DATABASE_URL", URL_PRUEBAS_POR_DEFECTO)
    if url == get_central_settings().database_url:
        pytest.exit(
            "CENTRAL_TEST_DATABASE_URL apunta a la base de desarrollo; las pruebas la "
            "borrarían. Usa una base distinta.",
            returncode=2,
        )
    return url


def _crear_base_si_falta(url: str) -> None:
    info = conninfo_to_dict(url)
    nombre = info["dbname"]
    mantenimiento = make_conninfo(url, dbname="postgres", connect_timeout=SEGUNDOS_ESPERA_CONEXION)
    with psycopg.connect(mantenimiento, autocommit=True) as conn:
        existe = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (nombre,)).fetchone()
        if not existe:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(nombre)))


@pytest.fixture(scope="session")
def url_central_pruebas() -> Iterator[str]:
    """Prepara la base de pruebas y hace que la app de central la use."""
    url = _url_pruebas()
    try:
        _crear_base_si_falta(url)
    except psycopg.OperationalError as error:
        pytest.skip(
            f"PostgreSQL no está disponible ({error}). Levántalo con `docker compose up -d`."
        )

    anterior = os.environ.get("CENTRAL_DATABASE_URL")
    os.environ["CENTRAL_DATABASE_URL"] = url
    get_central_settings.cache_clear()
    yield url
    if anterior is None:
        os.environ.pop("CENTRAL_DATABASE_URL", None)
    else:
        os.environ["CENTRAL_DATABASE_URL"] = anterior
    get_central_settings.cache_clear()


@pytest.fixture
def conn_central(url_central_pruebas: str) -> Iterator[psycopg.Connection]:
    """Reinicia los esquemas de la base de pruebas antes de cada prueba."""
    conn = abrir_conexion_central(url_central_pruebas)
    with conn.transaction():
        conn.execute("DROP SCHEMA IF EXISTS sincronizacion, usuarios, catalogo, inventario CASCADE")
    inicializar_esquema_central(conn)
    yield conn
    conn.close()

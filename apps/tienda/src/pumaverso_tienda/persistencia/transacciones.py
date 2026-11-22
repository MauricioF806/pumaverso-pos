"""Transacciones atómicas sobre la base de datos local.

Una transacción implementa el *punto de commit* del PRD: todo lo que se escribe
dentro de ``transaccion()`` se guarda completo al salir del bloque, o no se
guarda nada si ocurre un error o un apagón antes de terminar.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager


class TransaccionAnidadaError(RuntimeError):
    """Se intentó abrir una transacción dentro de otra ya abierta."""


@contextmanager
def transaccion(conexion: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Ejecuta un bloque como una sola transacción.

    Ejemplo::

        with transaccion(conexion):
            registrar_evento(conexion, ...)
            # otras escrituras de la misma operación

    Usa ``BEGIN IMMEDIATE`` para tomar el bloqueo de escritura desde el inicio.
    Así, dos operaciones nunca leen el mismo último consecutivo al mismo tiempo.

    Las transacciones no se anidan: una operación completa debe abrir una sola.
    """
    if conexion.in_transaction:
        raise TransaccionAnidadaError(
            "Ya hay una transacción abierta en esta conexión; "
            "una operación completa debe usar una sola transacción."
        )

    # Con autocommit=True, conexion.commit() y conexion.rollback() no hacen nada,
    # por eso se usan las sentencias SQL explícitas.
    conexion.execute("BEGIN IMMEDIATE")
    try:
        yield conexion
    except BaseException:
        # SQLite puede haber cancelado ya la transacción por su cuenta (por
        # ejemplo, con el disco lleno); en ese caso no hay nada que deshacer.
        if conexion.in_transaction:
            conexion.execute("ROLLBACK")
        raise

    try:
        conexion.execute("COMMIT")
    except BaseException:
        if conexion.in_transaction:
            conexion.execute("ROLLBACK")
        raise

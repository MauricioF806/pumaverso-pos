import sqlite3

import pytest

from pumaverso_tienda.persistencia import (
    MigracionError,
    TransaccionAnidadaError,
    abrir_conexion,
    aplicar_migraciones,
    transaccion,
    version_actual,
)
from pumaverso_tienda.persistencia import migraciones as modulo_migraciones


def test_abrir_conexion_crea_la_carpeta_y_configura_sqlite(ruta_bd):
    conexion = abrir_conexion(ruta_bd)

    assert ruta_bd.parent.is_dir()
    assert conexion.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conexion.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    assert conexion.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conexion.close()


def test_transaccion_guarda_todo_al_terminar(conexion):
    conexion.execute("CREATE TABLE prueba (valor INTEGER)")

    with transaccion(conexion):
        conexion.execute("INSERT INTO prueba VALUES (1)")
        conexion.execute("INSERT INTO prueba VALUES (2)")

    assert conexion.execute("SELECT COUNT(*) FROM prueba").fetchone()[0] == 2
    assert not conexion.in_transaction


def test_transaccion_no_guarda_nada_si_hay_un_error(conexion):
    conexion.execute("CREATE TABLE prueba (valor INTEGER)")

    with pytest.raises(RuntimeError, match="falla a la mitad"), transaccion(conexion):
        conexion.execute("INSERT INTO prueba VALUES (1)")
        raise RuntimeError("falla a la mitad")

    assert conexion.execute("SELECT COUNT(*) FROM prueba").fetchone()[0] == 0
    assert not conexion.in_transaction


def test_transaccion_no_se_puede_anidar(conexion):
    with transaccion(conexion), pytest.raises(TransaccionAnidadaError), transaccion(conexion):
        pass


def test_lo_no_confirmado_se_pierde_al_cerrar_de_golpe(ruta_bd):
    """Simula un apagón: lo que no llegó al COMMIT no existe al reiniciar."""
    conexion = abrir_conexion(ruta_bd)
    conexion.execute("CREATE TABLE prueba (valor INTEGER)")
    with transaccion(conexion):
        conexion.execute("INSERT INTO prueba VALUES (1)")

    conexion.execute("BEGIN IMMEDIATE")
    conexion.execute("INSERT INTO prueba VALUES (2)")
    conexion.close()  # se cierra sin COMMIT

    reabierta = abrir_conexion(ruta_bd)
    valores = [fila[0] for fila in reabierta.execute("SELECT valor FROM prueba")]
    reabierta.close()
    assert valores == [1]


def test_migraciones_crean_las_tablas(conexion):
    tablas = {
        fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"eventos", "estado_sincronizacion"} <= tablas
    assert version_actual(conexion) == len(modulo_migraciones.MIGRACIONES)


def test_migraciones_no_se_repiten(conexion):
    version = version_actual(conexion)

    assert aplicar_migraciones(conexion) == version
    assert aplicar_migraciones(conexion) == version


def test_migraciones_rechazan_una_base_de_datos_mas_nueva(conexion):
    conexion.execute("PRAGMA user_version = 999")

    with pytest.raises(MigracionError, match="más nueva"):
        aplicar_migraciones(conexion)


def test_migracion_que_falla_no_deja_cambios(ruta_bd, monkeypatch):
    conexion = abrir_conexion(ruta_bd)
    rota = "CREATE TABLE a_medias (x INTEGER); ESTO NO ES SQL;"
    monkeypatch.setattr(modulo_migraciones, "MIGRACIONES", ((1, rota),))

    with pytest.raises(sqlite3.OperationalError):
        aplicar_migraciones(conexion)

    assert version_actual(conexion) == 0
    tablas = [fila[0] for fila in conexion.execute("SELECT name FROM sqlite_master")]
    assert "a_medias" not in tablas
    assert not conexion.in_transaction
    conexion.close()


def test_migraciones_deben_estar_numeradas_sin_huecos(conexion, monkeypatch):
    monkeypatch.setattr(modulo_migraciones, "MIGRACIONES", ((1, "SELECT 1;"), (3, "SELECT 1;")))

    with pytest.raises(MigracionError, match="sin huecos"):
        aplicar_migraciones(conexion)

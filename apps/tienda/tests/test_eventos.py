import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from pumaverso_tienda.eventos import (
    ConfirmacionInvalidaError,
    ContenidoInvalidoError,
    FueraDeTransaccionError,
    TipoEvento,
    confirmar_hasta,
    eventos_pendientes,
    listar_eventos,
    obtener_evento,
    registrar_evento,
    ultimo_consecutivo,
    ultimo_consecutivo_confirmado,
    verificar_hash,
)
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones, transaccion

SUCURSAL = "S001"
CAJA = "C01"

AHORA = datetime(2026, 11, 21, 18, 30, tzinfo=UTC)


def registrar_venta(conexion, total_centavos=1800, *, caja=CAJA, ahora=AHORA):
    with transaccion(conexion):
        return registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=caja,
            tipo=TipoEvento.VENTA,
            contenido={"total_centavos": total_centavos},
            usuario_id="cajero-1",
            ahora=ahora,
        )


# --- Registro y consecutivo -------------------------------------------------


def test_registrar_evento_lo_guarda_completo(conexion):
    evento = registrar_venta(conexion)

    guardado = obtener_evento(conexion, SUCURSAL, CAJA, 1)
    assert guardado == evento
    assert guardado.tipo is TipoEvento.VENTA
    assert guardado.contenido == {"total_centavos": 1800}
    assert guardado.registrado_en == "2026-11-21T18:30:00.000+00:00"
    assert guardado.identidad == (SUCURSAL, CAJA, 1)


def test_consecutivos_empiezan_en_uno_y_no_tienen_huecos(conexion):
    consecutivos = [registrar_venta(conexion).consecutivo for _ in range(5)]

    assert consecutivos == [1, 2, 3, 4, 5]


def test_una_operacion_fallida_no_gasta_consecutivo(conexion):
    registrar_venta(conexion)

    with pytest.raises(RuntimeError), transaccion(conexion):
        registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo=TipoEvento.VENTA,
            contenido={"total_centavos": 500},
            usuario_id="cajero-1",
        )
        raise RuntimeError("se cayó la venta antes del cobro")

    assert ultimo_consecutivo(conexion, SUCURSAL, CAJA) == 1
    assert registrar_venta(conexion).consecutivo == 2


def test_evento_y_demas_escrituras_se_guardan_juntos(conexion):
    """El punto de commit: el evento existe solo si toda la operación terminó."""
    conexion.execute("CREATE TABLE existencias (sku TEXT PRIMARY KEY, cantidad INTEGER)")
    conexion.execute("INSERT INTO existencias VALUES ('coca-600', 10)")

    with pytest.raises(RuntimeError), transaccion(conexion):
        registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo=TipoEvento.VENTA,
            contenido={"sku": "coca-600", "cantidad": 1},
            usuario_id="cajero-1",
        )
        conexion.execute("UPDATE existencias SET cantidad = cantidad - 1")
        raise RuntimeError("apagón antes del commit")

    assert listar_eventos(conexion, SUCURSAL, CAJA) == []
    assert conexion.execute("SELECT cantidad FROM existencias").fetchone()[0] == 10


def test_cada_caja_tiene_su_propio_consecutivo(conexion):
    registrar_venta(conexion, caja="C01")
    registrar_venta(conexion, caja="C01")

    assert registrar_venta(conexion, caja="C02").consecutivo == 1


def test_registrar_fuera_de_transaccion_falla(conexion):
    with pytest.raises(FueraDeTransaccionError):
        registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo=TipoEvento.VENTA,
            contenido={},
            usuario_id="cajero-1",
        )


def test_los_eventos_confirmados_sobreviven_a_cerrar_la_conexion(ruta_bd, conexion):
    registrar_venta(conexion)
    conexion.close()

    reabierta = abrir_conexion(ruta_bd)
    assert ultimo_consecutivo(reabierta, SUCURSAL, CAJA) == 1
    reabierta.close()


# --- Append-only --------------------------------------------------------------


def test_no_se_puede_modificar_un_evento(conexion):
    registrar_venta(conexion)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conexion.execute("UPDATE eventos SET contenido = '{\"total_centavos\": 1}'")


def test_no_se_puede_borrar_un_evento(conexion):
    registrar_venta(conexion)

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conexion.execute("DELETE FROM eventos")

    assert ultimo_consecutivo(conexion, SUCURSAL, CAJA) == 1


def test_no_se_puede_repetir_un_consecutivo(conexion):
    evento = registrar_venta(conexion)

    with pytest.raises(sqlite3.IntegrityError):
        conexion.execute(
            "INSERT INTO eventos VALUES (?, ?, ?, 'venta', 1, '{}', 'x', 'x', 'x')",
            (SUCURSAL, CAJA, evento.consecutivo),
        )


# --- Hash ---------------------------------------------------------------------


def test_el_hash_es_el_mismo_para_el_mismo_contenido(ruta_bd, tmp_path):
    otra = abrir_conexion(tmp_path / "otra.db")
    aplicar_migraciones(otra)
    primera = abrir_conexion(ruta_bd)
    aplicar_migraciones(primera)

    with transaccion(primera):
        a = registrar_evento(
            primera,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo="venta",
            contenido={"b": 2, "a": 1},
            usuario_id="u",
            ahora=AHORA,
        )
    with transaccion(otra):
        b = registrar_evento(
            otra,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo=TipoEvento.VENTA,
            contenido={"a": 1, "b": 2},
            usuario_id="u",
            ahora=AHORA,
        )

    assert a.hash == b.hash
    assert len(a.hash) == 64
    primera.close()
    otra.close()


def test_verificar_hash_detecta_un_evento_alterado(conexion):
    evento = registrar_venta(conexion)

    assert verificar_hash(evento)
    assert not verificar_hash(replace(evento, contenido={"total_centavos": 1}))
    assert not verificar_hash(replace(evento, consecutivo=99))
    assert not verificar_hash(replace(evento, usuario_id="otro"))


# --- Validaciones ---------------------------------------------------------------


@pytest.mark.parametrize(
    "contenido",
    [
        ["no", "es", "diccionario"],
        {"importe": float("nan")},
        {"importe": float("inf")},
        {"objeto": object()},
        {1: "llave numérica"},
    ],
)
def test_contenido_invalido_se_rechaza(conexion, contenido):
    with pytest.raises(ContenidoInvalidoError), transaccion(conexion):
        registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo=TipoEvento.VENTA,
            contenido=contenido,
            usuario_id="cajero-1",
        )
    assert ultimo_consecutivo(conexion, SUCURSAL, CAJA) == 0


def test_tipo_desconocido_se_rechaza(conexion):
    with pytest.raises(ValueError), transaccion(conexion):
        registrar_evento(
            conexion,
            sucursal_id=SUCURSAL,
            caja_id=CAJA,
            tipo="regalo",
            contenido={},
            usuario_id="cajero-1",
        )


@pytest.mark.parametrize("campo", ["sucursal_id", "caja_id", "usuario_id"])
def test_identificadores_vacios_se_rechazan(conexion, campo):
    datos = {"sucursal_id": SUCURSAL, "caja_id": CAJA, "usuario_id": "cajero-1"}
    datos[campo] = "  "

    with pytest.raises(ValueError, match=campo), transaccion(conexion):
        registrar_evento(conexion, tipo=TipoEvento.VENTA, contenido={}, **datos)


def test_fecha_sin_zona_horaria_se_rechaza(conexion):
    with pytest.raises(ValueError, match="zona horaria"):
        registrar_venta(conexion, ahora=datetime(2026, 11, 21, 12, 0))


def test_fecha_se_guarda_en_utc(conexion):
    hora_cdmx = datetime(2026, 11, 21, 12, 30, tzinfo=timezone(timedelta(hours=-6)))

    evento = registrar_venta(conexion, ahora=hora_cdmx)

    assert evento.registrado_en == "2026-11-21T18:30:00.000+00:00"


# --- Estado de sincronización -----------------------------------------------------


def test_sin_confirmaciones_todos_los_eventos_estan_pendientes(conexion):
    for _ in range(3):
        registrar_venta(conexion)

    assert ultimo_consecutivo_confirmado(conexion, SUCURSAL, CAJA) == 0
    assert [e.consecutivo for e in eventos_pendientes(conexion, SUCURSAL, CAJA)] == [1, 2, 3]


def test_confirmar_hasta_deja_pendientes_solo_los_siguientes(conexion):
    for _ in range(5):
        registrar_venta(conexion)

    confirmar_hasta(conexion, SUCURSAL, CAJA, 3, ahora=AHORA)

    assert ultimo_consecutivo_confirmado(conexion, SUCURSAL, CAJA) == 3
    assert [e.consecutivo for e in eventos_pendientes(conexion, SUCURSAL, CAJA)] == [4, 5]


def test_confirmar_no_modifica_los_eventos(conexion):
    evento = registrar_venta(conexion)

    confirmar_hasta(conexion, SUCURSAL, CAJA, 1)

    assert obtener_evento(conexion, SUCURSAL, CAJA, 1) == evento


def test_confirmar_dos_veces_lo_mismo_no_cambia_nada(conexion):
    for _ in range(2):
        registrar_venta(conexion)

    confirmar_hasta(conexion, SUCURSAL, CAJA, 2)
    confirmar_hasta(conexion, SUCURSAL, CAJA, 2)

    assert ultimo_consecutivo_confirmado(conexion, SUCURSAL, CAJA) == 2


def test_la_confirmacion_no_retrocede(conexion):
    for _ in range(3):
        registrar_venta(conexion)
    confirmar_hasta(conexion, SUCURSAL, CAJA, 3)

    with pytest.raises(ConfirmacionInvalidaError, match="retroceder"):
        confirmar_hasta(conexion, SUCURSAL, CAJA, 1)

    assert ultimo_consecutivo_confirmado(conexion, SUCURSAL, CAJA) == 3


def test_no_se_puede_confirmar_un_evento_que_no_existe(conexion):
    registrar_venta(conexion)

    with pytest.raises(ConfirmacionInvalidaError, match="último registrado"):
        confirmar_hasta(conexion, SUCURSAL, CAJA, 5)


def test_eventos_pendientes_respeta_el_limite(conexion):
    for _ in range(5):
        registrar_venta(conexion)

    assert [e.consecutivo for e in eventos_pendientes(conexion, SUCURSAL, CAJA, limite=2)] == [
        1,
        2,
    ]


# --- Apagón real: el proceso muere de golpe --------------------------------------

SCRIPT_APAGON = """
import os, sys
from pumaverso_tienda.eventos import registrar_evento
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones, transaccion

conexion = abrir_conexion(sys.argv[1])
aplicar_migraciones(conexion)
with transaccion(conexion):
    registrar_evento(conexion, sucursal_id="S001", caja_id="C01", tipo="venta",
                     contenido={"total_centavos": 1800}, usuario_id="cajero-1")

conexion.execute("BEGIN IMMEDIATE")
registrar_evento(conexion, sucursal_id="S001", caja_id="C01", tipo="venta",
                 contenido={"total_centavos": 2500}, usuario_id="cajero-1")
os._exit(1)  # el proceso muere sin COMMIT, como en un apagón
"""


def test_un_apagon_a_mitad_de_venta_no_deja_la_venta_a_medias(ruta_bd):
    resultado = subprocess.run(
        [sys.executable, "-c", SCRIPT_APAGON, str(ruta_bd)],
        capture_output=True,
        check=False,
        timeout=30,
        # El proceso hijo debe encontrar el paquete aunque no esté instalado.
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
    )
    assert resultado.returncode == 1, resultado.stderr.decode()

    conexion = abrir_conexion(ruta_bd)
    eventos = listar_eventos(conexion, SUCURSAL, CAJA)
    conexion.close()

    assert [e.contenido["total_centavos"] for e in eventos] == [1800]
    assert all(verificar_hash(e) for e in eventos)

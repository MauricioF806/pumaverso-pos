import os
import time
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pumaverso_contratos import (
    ContenidoVenta,
    EventoContrato,
    LineaProducto,
    TipoEvento,
    calcular_hash_evento,
    formatear_registrado_en,
    serializar_contenido_evento,
)

from pumaverso_central.cola import (
    eventos_fallidos,
    procesar_eventos_pendientes_cola,
    recibir_lote_eventos,
)
from pumaverso_central.config import get_central_settings
from pumaverso_central.db import abrir_conexion_central
from pumaverso_central.main import app
from pumaverso_central.trabajador import TrabajadorCola

ADMIN = {"Authorization": f"Bearer {os.environ['CENTRAL_LLAVE_ADMIN']}"}

BASE = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)


def _crear_evento_venta(
    consecutivo: int, total: int = 1800, *, sucursal_id: str = "S001"
) -> EventoContrato:
    """Crea un evento de venta válido, con su hash real calculado como en la tienda."""
    contenido = serializar_contenido_evento(
        ContenidoVenta(
            partidas=[
                LineaProducto(
                    sku="coca-600",
                    cantidad=1,
                    precio_unitario_centavos=total,
                    subtotal_centavos=total,
                )
            ],
            total_centavos=total,
        )
    )
    datos = {
        "sucursal_id": sucursal_id,
        "caja_id": "C01",
        "consecutivo": consecutivo,
        "tipo": TipoEvento.VENTA,
        "version_esquema": 1,
        "contenido": contenido,
        "usuario_id": "cajero-1",
    }
    huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(BASE))
    return EventoContrato(**datos, registrado_en=BASE, hash=huella)


def _lote(*eventos: EventoContrato, sucursal_id: str = "S001") -> dict[str, object]:
    return {
        "sucursal_id": sucursal_id,
        "caja_id": "C01",
        "eventos": [e.model_dump(mode="json") for e in eventos],
    }


def test_ingesta_idempotente_conflicto_hash_y_huecos(conn_central):
    cliente = TestClient(app, headers=ADMIN)
    ev1 = _crear_evento_venta(1)
    ev3 = _crear_evento_venta(3)

    # Llega evento 1 y evento 3 (falta el 2 -> confirma hasta 1 y reporta hueco en 2).
    resp = cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev1.model_dump(mode="json"), ev3.model_dump(mode="json")],
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"ultimo_consecutivo_confirmado": 1, "hueco_detectado_desde": 2}

    # Reintento idempotente del evento 1 con mismo hash no duplica ni falla.
    resp_reintento = cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev1.model_dump(mode="json")],
        },
    )
    assert resp_reintento.status_code == 200

    # Reenvío del evento 1 con distinto hash devuelve conflicto 409.
    ev1_alterado = _crear_evento_venta(1, total=9999)
    resp_conflicto = cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev1_alterado.model_dump(mode="json")],
        },
    )
    assert resp_conflicto.status_code == 409

    # Llega el evento faltante 2 -> cierra el hueco y confirma hasta 3.
    ev2 = _crear_evento_venta(2)
    resp_cierre = cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev2.model_dump(mode="json")],
        },
    )
    assert resp_cierre.status_code == 200
    assert resp_cierre.json() == {"ultimo_consecutivo_confirmado": 3, "hueco_detectado_desde": None}


def test_cola_postgresql_actualiza_inventario_y_audita_usuario_revocado(conn_central):
    cliente = TestClient(app, headers=ADMIN)

    # Registramos cajero revocado y producto en central.
    cliente.put(
        "/usuarios/cajero-1",
        json={
            "usuario_id": "cajero-1",
            "nombre": "Ana López",
            "rol": "cajero",
            "hash_pin": "sal$hash",
            "activo": False,
        },
    )
    cliente.put(
        "/catalogo/productos/coca-600",
        json={
            "sku": "coca-600",
            "nombre": "Refresco Cola 600ml",
            "precio_centavos": 1800,
            "costo_centavos": 1100,
            "activo": True,
        },
    )

    ev1 = _crear_evento_venta(1)
    cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev1.model_dump(mode="json")],
        },
    )

    resultado = procesar_eventos_pendientes_cola(conn_central)
    assert resultado.procesados == 1
    assert resultado.fallidos == 0

    existencia = conn_central.execute(
        "SELECT cantidad FROM inventario.existencias_sucursal WHERE sucursal_id = 'S001'"
    ).fetchone()
    assert existencia is not None and existencia["cantidad"] == -1

    auditoria = conn_central.execute(
        "SELECT COUNT(*) AS total FROM inventario.cola_auditoria"
    ).fetchone()
    assert auditoria is not None and auditoria["total"] == 1


# --- Validación al recibir (revisión, falla 4) ----------------------------------


def test_rechaza_evento_con_hash_que_no_corresponde(conn_central):
    cliente = TestClient(app, headers=ADMIN)
    evento = _crear_evento_venta(1).model_copy(update={"hash": "0" * 64})

    resp = cliente.post("/sincronizacion/eventos", json=_lote(evento))

    assert resp.status_code == 422
    assert "hash no corresponde" in resp.json()["detail"]
    total = conn_central.execute(
        "SELECT COUNT(*) AS n FROM sincronizacion.eventos_recibidos"
    ).fetchone()["n"]
    assert total == 0


def test_rechaza_evento_con_contenido_que_no_cumple_el_contrato(conn_central):
    cliente = TestClient(app, headers=ADMIN)
    datos = {
        "sucursal_id": "S001",
        "caja_id": "C01",
        "consecutivo": 1,
        "tipo": "venta",
        "version_esquema": 1,
        "contenido": {"esto": "no es una venta"},
        "usuario_id": "x",
    }
    huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(BASE))
    evento = EventoContrato(**datos, registrado_en=BASE, hash=huella)

    resp = cliente.post("/sincronizacion/eventos", json=_lote(evento))

    assert resp.status_code == 422
    assert "Contenido inválido" in resp.json()["detail"]


def test_un_evento_invalido_rechaza_el_lote_completo(conn_central):
    cliente = TestClient(app, headers=ADMIN)
    malo = _crear_evento_venta(2).model_copy(update={"hash": "f" * 64})

    resp = cliente.post("/sincronizacion/eventos", json=_lote(_crear_evento_venta(1), malo))

    assert resp.status_code == 422
    estado = conn_central.execute(
        "SELECT COUNT(*) AS n FROM sincronizacion.eventos_recibidos"
    ).fetchone()["n"]
    assert estado == 0


# --- Cola aislada (revisión, falla 4) --------------------------------------------


def _encolar_evento_corrupto(conn_central) -> None:
    """Simula un evento que pasó la validación pero no se puede aplicar."""
    with conn_central.transaction():
        conn_central.execute(
            """
            INSERT INTO sincronizacion.cola_eventos (
                sucursal_id, caja_id, consecutivo, tipo, contenido, usuario_id, registrado_en
            ) VALUES ('S777', 'C01', 1, 'venta', %s, 'x', %s)
            """,
            (Jsonb({"esto": "no es una venta"}), BASE),
        )


def test_un_evento_que_falla_se_aparta_y_la_cola_sigue(conn_central):
    cliente = TestClient(app, headers=ADMIN)
    _encolar_evento_corrupto(conn_central)
    cliente.post("/sincronizacion/eventos", json=_lote(_crear_evento_venta(1)))

    resultado = procesar_eventos_pendientes_cola(conn_central)

    assert (resultado.procesados, resultado.fallidos) == (1, 1)
    existencia = conn_central.execute(
        "SELECT cantidad FROM inventario.existencias_sucursal WHERE sucursal_id = 'S001'"
    ).fetchone()
    assert existencia["cantidad"] == -1

    fallidos = cliente.get("/sincronizacion/cola/fallidos").json()
    assert [(f["sucursal_id"], f["consecutivo"]) for f in fallidos] == [("S777", 1)]
    assert "ValidationError" in fallidos[0]["error"]


def test_un_evento_fallido_no_se_reintenta_en_cada_ciclo(conn_central):
    _encolar_evento_corrupto(conn_central)
    procesar_eventos_pendientes_cola(conn_central)

    resultado = procesar_eventos_pendientes_cola(conn_central)

    assert (resultado.procesados, resultado.fallidos) == (0, 0)


def test_el_hash_de_la_tienda_coincide_con_el_de_central():
    """Un evento creado por la tienda debe pasar la verificación de central."""
    from pumaverso_central.cola import validar_evento_recibido

    validar_evento_recibido(_crear_evento_venta(1))


# --- Procesamiento automático de la cola (decisión 0007) -----------------------


class _RngTope:
    def uniform(self, _minimo: float, maximo: float) -> float:
        return maximo


def _trabajador_cola(url: str, *, lote: int = 50, intervalo: float = 5.0) -> TrabajadorCola:
    return TrabajadorCola(
        abrir_conexion=lambda: abrir_conexion_central(url),
        lote=lote,
        intervalo_segundos=intervalo,
        espera_maxima_segundos=60.0,
        rng=_RngTope(),
    )


def test_el_trabajador_vacia_la_cola_por_lotes(conn_central, url_central_pruebas):
    cliente = TestClient(app, headers=ADMIN)
    cliente.post(
        "/sincronizacion/eventos",
        json=_lote(_crear_evento_venta(1), _crear_evento_venta(2), _crear_evento_venta(3)),
    )
    trabajador = _trabajador_cola(url_central_pruebas, lote=2)

    esperas = [trabajador.ejecutar_ciclo(), trabajador.ejecutar_ciclo()]

    assert esperas == [0.0, 5.0]  # lote lleno: sigue de inmediato; después, intervalo normal
    assert trabajador.estado.procesados_total == 3
    existencia = conn_central.execute(
        "SELECT cantidad FROM inventario.existencias_sucursal WHERE sucursal_id = 'S001'"
    ).fetchone()
    assert existencia["cantidad"] == -3


def test_un_evento_apartado_no_cuenta_como_falla_del_trabajador(conn_central, url_central_pruebas):
    _encolar_evento_corrupto(conn_central)
    trabajador = _trabajador_cola(url_central_pruebas)

    espera = trabajador.ejecutar_ciclo()

    assert espera == 5.0
    assert trabajador.estado.fallidos_total == 1
    assert trabajador.estado.fallos_consecutivos == 0


def test_si_postgresql_falla_reintenta_con_espera_creciente():
    def sin_base():
        raise psycopg.OperationalError("connection refused")

    trabajador = TrabajadorCola(
        abrir_conexion=sin_base,
        lote=50,
        intervalo_segundos=5.0,
        espera_maxima_segundos=8.0,
        rng=_RngTope(),
    )

    esperas = [trabajador.ejecutar_ciclo() for _ in range(5)]

    assert esperas == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert trabajador.estado.fallos_consecutivos == 5
    assert "connection refused" in trabajador.estado.ultimo_error


def test_la_app_procesa_la_cola_sola(conn_central, monkeypatch):
    monkeypatch.setenv("CENTRAL_COLA_AUTOMATICA", "true")
    monkeypatch.setenv("CENTRAL_COLA_INTERVALO_SEGUNDOS", "0.05")
    get_central_settings.cache_clear()
    try:
        with TestClient(app, headers=ADMIN) as cliente:
            cliente.post("/sincronizacion/eventos", json=_lote(_crear_evento_venta(1)))
            limite = time.monotonic() + 5
            estado = cliente.get("/sincronizacion/cola/trabajador").json()
            while estado["procesados_total"] < 1 and time.monotonic() < limite:
                time.sleep(0.05)
                estado = cliente.get("/sincronizacion/cola/trabajador").json()

        assert estado["automatico"] is True
        assert estado["procesados_total"] == 1
        assert estado["fallos_consecutivos"] == 0
    finally:
        get_central_settings.cache_clear()


# --- Script de datos de demostración -------------------------------------------


def test_el_script_de_demo_carga_usuarios_y_productos_en_central(conn_central):
    pytest.importorskip("pumaverso_tienda", reason="El script usa el hash de PIN de la tienda.")
    import importlib.util
    from pathlib import Path

    ruta = Path(__file__).resolve().parents[3] / "scripts" / "cargar_datos_demo.py"
    especificacion = importlib.util.spec_from_file_location("cargar_datos_demo", ruta)
    demo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(demo)

    cliente = TestClient(app, headers=ADMIN)
    demo.cargar_central(cliente)
    demo.cargar_central(cliente)  # correrlo dos veces no duplica

    usuarios = conn_central.execute("SELECT COUNT(*) AS n FROM usuarios.usuarios").fetchone()["n"]
    productos = conn_central.execute("SELECT COUNT(*) AS n FROM catalogo.productos").fetchone()["n"]
    assert (usuarios, productos) == (len(demo.USUARIOS), len(demo.PRODUCTOS))


# --- Procesamiento concurrente de la cola ----------------------------------------------


def _venta_de_varios(consecutivo: int, skus: list[str]) -> EventoContrato:
    contenido = serializar_contenido_evento(
        ContenidoVenta(
            partidas=[
                LineaProducto(
                    sku=s, cantidad=1, precio_unitario_centavos=100, subtotal_centavos=100
                )
                for s in skus
            ],
            total_centavos=100 * len(skus),
        )
    )
    datos = {
        "sucursal_id": "S001",
        "caja_id": "C01",
        "consecutivo": consecutivo,
        "tipo": TipoEvento.VENTA,
        "version_esquema": 1,
        "contenido": contenido,
        "usuario_id": "cajero-1",
    }
    huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(BASE))
    return EventoContrato(**datos, registrado_en=BASE, hash=huella)


def test_dos_procesos_aplicando_la_cola_a_la_vez_no_apartan_eventos(
    conn_central, url_central_pruebas, monkeypatch
):
    """El trabajador y /procesar-cola pueden coincidir; no deben bloquearse entre sí.

    Cada proceso toma dos ventas: uno vende A y luego B; el otro, B y luego A.
    Una barrera hace que ambos terminen su primera venta antes de seguir, que es
    justo el cruce que bloqueaba a PostgreSQL.
    """
    import threading

    from pumaverso_central import cola

    eventos = [_venta_de_varios(n, [sku]) for n, sku in enumerate("ABBA", start=1)]
    recibir_lote_eventos(conn_central, sucursal_id="S001", caja_id="C01", eventos=eventos)

    barrera = threading.Barrier(2)
    original = cola._aplicar_evento_en_central
    primera_vez = threading.local()

    def aplicar_y_esperar_al_otro(conn, fila):
        original(conn, fila)
        if not getattr(primera_vez, "hecha", False):
            primera_vez.hecha = True
            try:
                barrera.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass  # el otro proceso está esperando su turno: bien

    monkeypatch.setattr(cola, "_aplicar_evento_en_central", aplicar_y_esperar_al_otro)
    resultados = []

    def procesar():
        with abrir_conexion_central(url_central_pruebas) as conn:
            resultados.append(procesar_eventos_pendientes_cola(conn, limite=2))

    hilos = [threading.Thread(target=procesar) for _ in range(2)]
    for hilo in hilos:
        hilo.start()
    for hilo in hilos:
        hilo.join(timeout=30)

    assert eventos_fallidos(conn_central) == []
    assert sum(r.procesados for r in resultados) == 4
    cantidades = conn_central.execute(
        "SELECT sku, cantidad FROM inventario.existencias_sucursal ORDER BY sku"
    ).fetchall()
    assert [(c["sku"], c["cantidad"]) for c in cantidades] == [("A", -2), ("B", -2)]


def test_un_error_pasajero_de_postgresql_no_aparta_el_evento(conn_central, monkeypatch):
    """Un bloqueo mutuo no es culpa del evento: se reintenta el lote, no se aparta."""
    from pumaverso_central import cola

    original = cola._aplicar_evento_en_central
    llamadas = {"n": 0}

    def falla_una_vez(conn, fila):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise psycopg.errors.DeadlockDetected("deadlock detected")
        original(conn, fila)

    monkeypatch.setattr(cola, "_aplicar_evento_en_central", falla_una_vez)
    recibir_lote_eventos(
        conn_central, sucursal_id="S001", caja_id="C01", eventos=[_crear_evento_venta(1)]
    )

    with pytest.raises(psycopg.errors.DeadlockDetected):
        procesar_eventos_pendientes_cola(conn_central)
    reintento = procesar_eventos_pendientes_cola(conn_central)

    assert reintento.procesados == 1
    assert eventos_fallidos(conn_central) == []

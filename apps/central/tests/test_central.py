from datetime import UTC, datetime

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

from pumaverso_central.cola import procesar_eventos_pendientes_cola
from pumaverso_central.main import app

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
    cliente = TestClient(app)
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
    cliente = TestClient(app)

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
    cliente = TestClient(app)
    evento = _crear_evento_venta(1).model_copy(update={"hash": "0" * 64})

    resp = cliente.post("/sincronizacion/eventos", json=_lote(evento))

    assert resp.status_code == 422
    assert "hash no corresponde" in resp.json()["detail"]
    total = conn_central.execute(
        "SELECT COUNT(*) AS n FROM sincronizacion.eventos_recibidos"
    ).fetchone()["n"]
    assert total == 0


def test_rechaza_evento_con_contenido_que_no_cumple_el_contrato(conn_central):
    cliente = TestClient(app)
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
    cliente = TestClient(app)
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
    cliente = TestClient(app)
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

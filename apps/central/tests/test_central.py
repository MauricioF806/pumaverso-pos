from datetime import UTC, datetime

from fastapi.testclient import TestClient
from pumaverso_contratos import (
    ContenidoVenta,
    EventoContrato,
    LineaProducto,
    TipoEvento,
    serializar_contenido_evento,
)

from pumaverso_central.cola import procesar_eventos_pendientes_cola
from pumaverso_central.main import app

BASE = datetime(2026, 11, 25, 20, 0, tzinfo=UTC)


def _crear_evento_venta(consecutivo: int, total: int = 1800, hash_char: str = "a") -> EventoContrato:
    contenido = ContenidoVenta(
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
    return EventoContrato(
        sucursal_id="S001",
        caja_id="C01",
        consecutivo=consecutivo,
        tipo=TipoEvento.VENTA,
        contenido=serializar_contenido_evento(contenido),
        usuario_id="cajero-1",
        registrado_en=BASE,
        hash=hash_char * 64,
    )


def test_ingesta_idempotente_conflicto_hash_y_huecos(conn_central):
    cliente = TestClient(app)
    ev1 = _crear_evento_venta(1, hash_char="1")
    ev3 = _crear_evento_venta(3, hash_char="3")

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
    ev1_alterado = _crear_evento_venta(1, total=9999, hash_char="9")
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
    ev2 = _crear_evento_venta(2, hash_char="2")
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

    ev1 = _crear_evento_venta(1, hash_char="a")
    cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [ev1.model_dump(mode="json")],
        },
    )

    procesados = procesar_eventos_pendientes_cola(conn_central)
    assert procesados == 1

    existencia = conn_central.execute(
        "SELECT cantidad FROM inventario.existencias_sucursal WHERE sucursal_id = 'S001'"
    ).fetchone()
    assert existencia is not None and existencia["cantidad"] == -1

    auditoria = conn_central.execute("SELECT COUNT(*) AS total FROM inventario.cola_auditoria").fetchone()
    assert auditoria is not None and auditoria["total"] == 1
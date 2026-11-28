"""Cola de auditoría: consultar y marcar como revisado (decisión 0012)."""

import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
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

ADMIN = {"Authorization": f"Bearer {os.environ['CENTRAL_LLAVE_ADMIN']}"}
LLAVE_S001 = "llave-de-la-sucursal-s001-para-pruebas"
MOMENTO = datetime(2026, 11, 26, 15, 30, tzinfo=UTC)


def _venta(consecutivo: int, usuario_id: str) -> dict:
    datos = {
        "sucursal_id": "S001",
        "caja_id": "C01",
        "consecutivo": consecutivo,
        "tipo": TipoEvento.VENTA,
        "version_esquema": 1,
        "contenido": serializar_contenido_evento(
            ContenidoVenta(
                partidas=[
                    LineaProducto(
                        sku="leche",
                        cantidad=2,
                        precio_unitario_centavos=3000,
                        subtotal_centavos=6000,
                    )
                ],
                total_centavos=6000,
            )
        ),
        "usuario_id": usuario_id,
    }
    huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(MOMENTO))
    return EventoContrato(**datos, registrado_en=MOMENTO, hash=huella).model_dump(mode="json")


@pytest.fixture
def con_operacion_auditada(conn_central):
    """Un cajero dado de baja en central vendió sin conexión antes de enterarse."""
    cliente = TestClient(app, headers=ADMIN)
    for usuario_id, activo in (("cajero-baja", False), ("cajero-activo", True)):
        cliente.put(
            f"/usuarios/{usuario_id}",
            json={
                "usuario_id": usuario_id,
                "nombre": usuario_id,
                "rol": "cajero",
                "hash_pin": "sal$hash",
                "activo": activo,
            },
        )
    cliente.put("/sucursales/S001/llave", json={"llave": LLAVE_S001})
    cliente.post(
        "/sincronizacion/eventos",
        json={
            "sucursal_id": "S001",
            "caja_id": "C01",
            "eventos": [_venta(1, "cajero-baja"), _venta(2, "cajero-activo")],
        },
    )
    procesar_eventos_pendientes_cola(conn_central)
    return cliente


def test_muestra_que_hizo_el_usuario_dado_de_baja(con_operacion_auditada):
    registros = con_operacion_auditada.get("/auditoria").json()

    assert len(registros) == 1  # la venta del cajero activo no se aparta
    registro = registros[0]
    assert registro["usuario_id"] == "cajero-baja"
    assert registro["tipo"] == "venta"
    assert registro["total_centavos"] == 6000
    assert registro["consecutivo"] == 1
    assert registro["revisada_en"] is None


def test_marcar_revisado_lo_saca_de_pendientes_y_deja_constancia(con_operacion_auditada):
    registro_id = con_operacion_auditada.get("/auditoria").json()[0]["id"]
    revision = {
        "revisada_por": "regional1",
        "nota": "Venta legítima: el cajero no sabía de su baja.",
    }

    respuesta = con_operacion_auditada.post(f"/auditoria/{registro_id}/revisar", json=revision)
    otra_vez = con_operacion_auditada.post(f"/auditoria/{registro_id}/revisar", json=revision)

    assert respuesta.status_code == 200
    assert otra_vez.status_code == 404
    assert con_operacion_auditada.get("/auditoria").json() == []
    historial = con_operacion_auditada.get("/auditoria", params={"incluir_revisadas": True}).json()
    assert historial[0]["revisada_por"] == "regional1"
    assert historial[0]["nota"].startswith("Venta legítima")


def test_se_puede_filtrar_por_sucursal(con_operacion_auditada):
    assert len(con_operacion_auditada.get("/auditoria", params={"sucursal_id": "S001"}).json()) == 1
    assert con_operacion_auditada.get("/auditoria", params={"sucursal_id": "S002"}).json() == []


def test_una_sucursal_no_puede_ver_ni_revisar_la_auditoria(con_operacion_auditada):
    sucursal = {"Authorization": f"Bearer {LLAVE_S001}"}

    ver = TestClient(app).get("/auditoria", headers=sucursal)
    revisar = TestClient(app).post(
        "/auditoria/1/revisar", headers=sucursal, json={"revisada_por": "x", "nota": "x"}
    )

    assert ver.status_code == 403
    assert revisar.status_code == 403

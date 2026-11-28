"""Credenciales de la API de central (decisión 0010)."""

import hashlib
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

from pumaverso_central import main
from pumaverso_central.config import get_central_settings
from pumaverso_central.main import app

ADMIN = {"Authorization": f"Bearer {os.environ['CENTRAL_LLAVE_ADMIN']}"}
LLAVE_S001 = "llave-de-la-sucursal-s001-para-pruebas"
LLAVE_S002 = "llave-de-la-sucursal-s002-para-pruebas"


def _con(llave: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {llave}"}


@pytest.fixture
def central(conn_central):
    cliente = TestClient(app)
    for sucursal, llave in (("S001", LLAVE_S001), ("S002", LLAVE_S002)):
        respuesta = cliente.put(
            f"/sucursales/{sucursal}/llave", headers=ADMIN, json={"llave": llave}
        )
        assert respuesta.status_code == 200
    return cliente


def _lote_de(sucursal: str) -> dict:
    """Un lote válido con una venta de esa sucursal."""
    momento = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    datos = {
        "sucursal_id": sucursal,
        "caja_id": "C01",
        "consecutivo": 1,
        "tipo": TipoEvento.VENTA,
        "version_esquema": 1,
        "contenido": serializar_contenido_evento(
            ContenidoVenta(
                partidas=[
                    LineaProducto(
                        sku="leche", cantidad=1, precio_unitario_centavos=100, subtotal_centavos=100
                    )
                ],
                total_centavos=100,
            )
        ),
        "usuario_id": "cajero1",
    }
    huella = calcular_hash_evento(**datos, registrado_en=formatear_registrado_en(momento))
    evento = EventoContrato(**datos, registrado_en=momento, hash=huella)
    return {"sucursal_id": sucursal, "caja_id": "C01", "eventos": [evento.model_dump(mode="json")]}


# --- Sin llave o con una llave que no existe ------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("get", "/sincronizacion/actualizaciones?sucursal_id=S001"),
        ("post", "/sincronizacion/eventos"),
        ("put", "/usuarios/intruso"),
        ("put", "/catalogo/productos/123"),
        ("post", "/sincronizacion/procesar-cola"),
        ("get", "/sincronizacion/cola/fallidos"),
        ("get", "/inventario/fantasma/sospechas"),
        ("put", "/sucursales/S001/llave"),
    ],
)
def test_sin_llave_central_no_responde_nada(central, metodo, ruta):
    respuesta = getattr(central, metodo)(ruta)

    assert respuesta.status_code == 401
    assert respuesta.headers["www-authenticate"] == "Bearer"


def test_una_llave_inventada_no_sirve(central):
    respuesta = central.get(
        "/sincronizacion/actualizaciones",
        params={"sucursal_id": "S001"},
        headers=_con("una-llave-que-nadie-registro-nunca-jamas"),
    )

    assert respuesta.status_code == 401


def test_health_no_necesita_llave(central):
    assert central.get("/health").status_code == 200


def test_si_central_no_tiene_llave_de_admin_lo_dice(central, monkeypatch):
    # Se quita en la configuración ya cargada: borrar la variable no basta si el
    # .env de quien corre las pruebas también la trae.
    sin_llave = get_central_settings().model_copy(update={"llave_admin": None})
    monkeypatch.setattr(main, "get_central_settings", lambda: sin_llave)

    respuesta = central.put("/usuarios/x", headers=ADMIN, json={})

    assert respuesta.status_code == 401
    assert "CENTRAL_LLAVE_ADMIN" in respuesta.json()["detail"]


# --- Llave de sucursal: solo su sucursal y solo sincronizar -----------------------------


def test_la_llave_de_una_sucursal_sirve_para_sincronizar_la_suya(central):
    bajada = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S001"}, headers=_con(LLAVE_S001)
    )
    subida = central.post(
        "/sincronizacion/eventos", json=_lote_de("S001"), headers=_con(LLAVE_S001)
    )

    assert bajada.status_code == 200
    assert subida.status_code == 200
    assert subida.json()["ultimo_consecutivo_confirmado"] == 1


def test_una_sucursal_no_puede_hacerse_pasar_por_otra(central, conn_central):
    bajada = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S002"}, headers=_con(LLAVE_S001)
    )
    subida = central.post(
        "/sincronizacion/eventos", json=_lote_de("S002"), headers=_con(LLAVE_S001)
    )

    assert bajada.status_code == 403
    assert subida.status_code == 403
    assert "S002" in subida.json()["detail"]
    assert (
        conn_central.execute(
            "SELECT COUNT(*) AS n FROM sincronizacion.eventos_recibidos"
        ).fetchone()["n"]
        == 0
    )


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("put", "/usuarios/intruso"),
        ("put", "/catalogo/productos/123"),
        ("post", "/sincronizacion/procesar-cola"),
        ("get", "/sincronizacion/cola/fallidos"),
        ("get", "/sincronizacion/cola/trabajador"),
        ("post", "/inventario/fantasma/detectar"),
        ("put", "/sucursales/S001/llave"),
    ],
)
def test_una_sucursal_no_puede_administrar(central, metodo, ruta):
    respuesta = getattr(central, metodo)(ruta, headers=_con(LLAVE_S001))

    assert respuesta.status_code == 403


# --- Administración de llaves ----------------------------------------------------------


def test_central_genera_la_llave_y_solo_guarda_su_huella(central, conn_central):
    respuesta = central.put("/sucursales/S003/llave", headers=ADMIN)

    llave = respuesta.json()["llave"]
    guardada = conn_central.execute(
        "SELECT huella_llave FROM sincronizacion.credenciales_sucursal WHERE sucursal_id = 'S003'"
    ).fetchone()["huella_llave"]
    assert len(llave) >= 32
    assert guardada == hashlib.sha256(llave.encode()).hexdigest()
    assert llave not in guardada
    usar = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S003"}, headers=_con(llave)
    )
    assert usar.status_code == 200


def test_cambiar_la_llave_invalida_la_anterior(central):
    central.put("/sucursales/S001/llave", headers=ADMIN, json={"llave": "n" * 40})

    vieja = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S001"}, headers=_con(LLAVE_S001)
    )
    nueva = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S001"}, headers=_con("n" * 40)
    )

    assert vieja.status_code == 401
    assert nueva.status_code == 200


def test_dos_sucursales_no_pueden_compartir_llave(central):
    respuesta = central.put("/sucursales/S003/llave", headers=ADMIN, json={"llave": LLAVE_S001})

    assert respuesta.status_code == 409


def test_una_llave_corta_se_rechaza(central):
    respuesta = central.put("/sucursales/S003/llave", headers=ADMIN, json={"llave": "1234"})

    assert respuesta.status_code == 422


def test_revocar_la_llave_corta_el_acceso(central):
    revocar = central.delete("/sucursales/S001/llave", headers=ADMIN)
    otra_vez = central.delete("/sucursales/S001/llave", headers=ADMIN)
    usar = central.get(
        "/sincronizacion/actualizaciones", params={"sucursal_id": "S001"}, headers=_con(LLAVE_S001)
    )

    assert revocar.status_code == 204
    assert otra_vez.status_code == 404
    assert usar.status_code == 401


# --- Inventario fantasma: cada sucursal ve y cierra solo lo suyo ------------------------


def _sospecha(conn, sucursal: str) -> int:
    ahora = datetime.now(UTC)
    with conn.transaction():
        return conn.execute(
            """
            INSERT INTO inventario.sospechas_fantasma (
                sucursal_id, sku, franja_inicio, franja_fin, unidades_esperadas,
                unidades_vendidas, umbral_unidades, probabilidad, dias_historia,
                existencia_registrada, estado, detectada_en
            ) VALUES (%s, 'leche', %s, %s, 6, 0, 0, 0.002, 14, 30, 'pendiente_conteo', %s)
            RETURNING id
            """,
            (sucursal, ahora, ahora, ahora),
        ).fetchone()["id"]


def test_una_sucursal_solo_ve_sus_sospechas(central, conn_central):
    _sospecha(conn_central, "S001")
    _sospecha(conn_central, "S002")

    propias = central.get("/inventario/fantasma/sospechas", headers=_con(LLAVE_S001))
    ajenas = central.get(
        "/inventario/fantasma/sospechas", params={"sucursal_id": "S002"}, headers=_con(LLAVE_S001)
    )
    todas = central.get("/inventario/fantasma/sospechas", headers=ADMIN)

    assert [s["sucursal_id"] for s in propias.json()["sospechas"]] == ["S001"]
    assert ajenas.status_code == 403
    assert len(todas.json()["sospechas"]) == 2


def test_una_sucursal_no_puede_cerrar_sospechas_de_otra(central, conn_central):
    de_s002 = _sospecha(conn_central, "S002")
    conteo = {"cantidad_contada": 0, "usuario_id": "gerente1"}

    ajena = central.post(
        f"/inventario/fantasma/sospechas/{de_s002}/conteo", json=conteo, headers=_con(LLAVE_S001)
    )
    propia = central.post(
        f"/inventario/fantasma/sospechas/{de_s002}/conteo", json=conteo, headers=_con(LLAVE_S002)
    )

    assert ajena.status_code == 404  # ni siquiera confirma que exista
    assert propia.status_code == 200

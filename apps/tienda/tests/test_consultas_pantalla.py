"""Consultas y CORS que necesita la pantalla de caja (decisión 0008)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.usuarios import generar_hash_pin, guardar_usuario_local

ORIGEN_PANTALLA = "http://localhost:5173"


@pytest.fixture
def tienda(tmp_path, monkeypatch):
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    get_settings.cache_clear()

    con = abrir_conexion(ruta)
    aplicar_migraciones(con)
    ahora = datetime.now(UTC)
    for usuario_id, nombre, rol, pin in (
        ("cajero-1", "Ana López", "cajero", "1234"),
        ("gerente-1", "Rosa Díaz", "gerente", "9999"),
    ):
        guardar_usuario_local(
            con,
            usuario_id=usuario_id,
            nombre=nombre,
            rol=rol,
            hash_pin=generar_hash_pin(pin),
            actualizado_en=ahora,
        )
    for sku, nombre, precio, activo, recibido in (
        ("coca-600", "Coca-Cola 600 ml", 1800, True, ahora),
        ("coca-355", "Coca-Cola lata 355 ml", 1500, True, ahora),
        ("sabritas-45", "Sabritas Originales 45 g", 2100, True, ahora),
        ("gansito", "Gansito Marinela", 1900, True, ahora - timedelta(hours=80)),
        ("descontinuado", "Coca-Cola retornable", 1200, False, ahora),
        ("pct_100", "Producto 100% raro", 100, True, ahora),
    ):
        guardar_producto(
            con,
            sku=sku,
            nombre=nombre,
            precio_centavos=precio,
            precio_actualizado_en=recibido,
            costo_centavos=precio // 2,
            costo_actualizado_en=recibido,
            activo=activo,
        )

    with TestClient(app) as cliente:
        yield cliente
    con.close()
    get_settings.cache_clear()


def _sesion(cliente, usuario_id="cajero-1", pin="1234") -> dict[str, str]:
    token = cliente.post("/auth/login", json={"usuario_id": usuario_id, "pin": pin}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _vender(cliente, sesion, sku="coca-600", cantidad=1) -> dict:
    resp = cliente.post(
        "/pos/ventas", headers=sesion, json={"items": [{"sku": sku, "cantidad": cantidad}]}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- CORS -----------------------------------------------------------------------


def test_cors_permite_a_la_pantalla_de_caja(tienda):
    resp = tienda.options(
        "/pos/ventas",
        headers={
            "Origin": ORIGEN_PANTALLA,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == ORIGEN_PANTALLA
    assert "authorization" in resp.headers["access-control-allow-headers"].lower()


def test_cors_no_permite_otros_sitios(tienda):
    resp = tienda.options(
        "/pos/ventas",
        headers={"Origin": "https://sitio-ajeno.com", "Access-Control-Request-Method": "POST"},
    )

    assert "access-control-allow-origin" not in resp.headers


# --- ¿Quién soy? ------------------------------------------------------------------


def test_yo_devuelve_al_usuario_de_la_sesion(tienda):
    resp = tienda.get("/auth/yo", headers=_sesion(tienda))

    assert resp.status_code == 200
    assert resp.json() == {"usuario_id": "cajero-1", "nombre": "Ana López", "rol": "cajero"}


def test_yo_sin_sesion_responde_401(tienda):
    assert tienda.get("/auth/yo").status_code == 401


# --- Búsqueda en el catálogo ------------------------------------------------------


def test_busca_por_nombre_sin_importar_mayusculas(tienda):
    resp = tienda.get("/catalogo", params={"buscar": "COCA"})

    assert [p["sku"] for p in resp.json()] == ["coca-600", "coca-355"]


def test_busca_por_sku(tienda):
    resp = tienda.get("/catalogo", params={"buscar": "sabritas"})

    assert [p["sku"] for p in resp.json()] == ["sabritas-45"]


def test_no_muestra_productos_inactivos(tienda):
    skus = [p["sku"] for p in tienda.get("/catalogo", params={"buscar": "retornable"}).json()]

    assert skus == []


def test_los_comodines_se_buscan_como_texto(tienda):
    """Escribir % no debe devolver todo el catálogo."""
    skus = [p["sku"] for p in tienda.get("/catalogo", params={"buscar": "%"}).json()]

    assert skus == ["pct_100"]


def test_sin_texto_lista_en_orden_alfabetico_con_limite(tienda):
    resp = tienda.get("/catalogo", params={"limite": 2})

    assert [p["nombre"] for p in resp.json()] == ["Coca-Cola 600 ml", "Coca-Cola lata 355 ml"]


def test_indica_si_el_precio_ya_no_esta_vigente(tienda):
    (gansito,) = tienda.get("/catalogo", params={"buscar": "gansito"}).json()

    assert gansito["precio_vigente"] is False


def test_muestra_la_existencia(tienda):
    sesion = _sesion(tienda)
    tienda.post(
        "/inventario/recepciones",
        headers=sesion,
        json={
            "proveedor_id": "PROV01",
            "folio_documento": "F-1",
            "partidas": [{"sku": "coca-600", "cantidad": 12, "costo_unitario_centavos": 900}],
        },
    )
    _vender(tienda, sesion, cantidad=2)

    (coca,) = tienda.get("/catalogo", params={"buscar": "600"}).json()

    assert coca["existencia"] == 10


# --- Ventas recientes y detalle ---------------------------------------------------


def test_ventas_recientes_requieren_sesion(tienda):
    assert tienda.get("/pos/ventas").status_code == 401


def test_ventas_recientes_de_la_mas_nueva_a_la_mas_vieja(tienda):
    sesion = _sesion(tienda)
    primera = _vender(tienda, sesion, "coca-600", 1)
    segunda = _vender(tienda, sesion, "sabritas-45", 2)

    ventas = tienda.get("/pos/ventas", headers=sesion).json()

    assert [v["consecutivo"] for v in ventas] == [segunda["consecutivo"], primera["consecutivo"]]
    assert ventas[0]["total_centavos"] == 4200
    assert ventas[0]["partidas"] == [
        {
            "sku": "sabritas-45",
            "nombre": "Sabritas Originales 45 g",
            "cantidad": 2,
            "precio_unitario_centavos": 2100,
            "subtotal_centavos": 4200,
        }
    ]
    assert ventas[0]["usuario_id"] == "cajero-1"


def test_ventas_recientes_respeta_el_limite(tienda):
    sesion = _sesion(tienda)
    for _ in range(4):
        _vender(tienda, sesion)

    ventas = tienda.get("/pos/ventas", headers=sesion, params={"limite": 3}).json()

    assert len(ventas) == 3


def test_una_venta_cancelada_aparece_marcada(tienda):
    sesion = _sesion(tienda)
    venta = _vender(tienda, sesion)
    cancelacion = tienda.post(
        f"/pos/ventas/{venta['consecutivo']}/cancelar",
        headers=sesion,
        json={"motivo": "Devolución", "aprobador_id": "gerente-1", "aprobador_pin": "9999"},
    ).json()

    ventas = tienda.get("/pos/ventas", headers=sesion).json()

    # La cancelación es un evento aparte: no aparece como venta.
    assert [v["consecutivo"] for v in ventas] == [venta["consecutivo"]]
    assert ventas[0]["cancelada"] is True
    assert ventas[0]["consecutivo_cancelacion"] == cancelacion["consecutivo"]


def test_detalle_de_una_venta(tienda):
    sesion = _sesion(tienda)
    venta = _vender(tienda, sesion, "coca-355", 3)

    detalle = tienda.get(f"/pos/ventas/{venta['consecutivo']}", headers=sesion).json()

    assert detalle["total_centavos"] == 4500
    assert detalle["hash"] == venta["hash"]
    assert detalle["cancelada"] is False


def test_detalle_de_algo_que_no_es_una_venta_responde_404(tienda):
    sesion = _sesion(tienda)
    tienda.post(
        "/inventario/mermas",
        headers=sesion,
        json={"sku": "coca-600", "cantidad": 1, "motivo": "Se rompió"},
    )

    assert tienda.get("/pos/ventas/1", headers=sesion).status_code == 404
    assert tienda.get("/pos/ventas/99", headers=sesion).status_code == 404

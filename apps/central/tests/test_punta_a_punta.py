"""Escenarios de falla de la demo con la tienda y central reales (paso 8).

La tienda usa su API y su SQLite de verdad; central, su API y PostgreSQL. Entre
las dos hay una "red" de prueba que se puede cortar, o que deja llegar la
petición a central pero pierde la respuesta. Así se comprueba lo que promete
el PRD: la caja vende sin central, no pierde ventas y no las duplica.
"""

import importlib.util
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

pytest.importorskip("pumaverso_tienda", reason="Estas pruebas levantan también la tienda.")

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.main import app as app_tienda
from pumaverso_tienda.sincronizacion import TransporteCentralHttp

from pumaverso_central.main import app as app_central

RUTA_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "cargar_datos_demo.py"
REFRESCO = "7501055300846"


def _cargar_script_demo():
    especificacion = importlib.util.spec_from_file_location("cargar_datos_demo", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


demo = _cargar_script_demo()


class Red:
    """Conexión de prueba entre la tienda y central que se puede descomponer."""

    def __init__(self, central: TestClient) -> None:
        self._central = central
        self.cortada = False
        self.perder_respuesta_de_subida = False

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        if self.cortada:
            raise httpx.ConnectError("Central no responde.", request=peticion)
        respuesta = self._central.request(
            peticion.method,
            peticion.url.raw_path.decode(),
            content=peticion.content,
            headers={"content-type": peticion.headers.get("content-type", "application/json")},
        )
        es_subida = peticion.method == "POST" and peticion.url.path == "/sincronizacion/eventos"
        if es_subida and self.perder_respuesta_de_subida:
            # Central ya guardó los eventos, pero la confirmación nunca llega a la tienda.
            raise httpx.ReadTimeout("Se perdió la respuesta de central.", request=peticion)
        return httpx.Response(
            respuesta.status_code, headers=respuesta.headers, content=respuesta.content
        )


@pytest.fixture
def sistema(conn_central, tmp_path, monkeypatch) -> Iterator[Callable[[], tuple]]:
    """Central con los datos de demo, una tienda con 24 piezas y la red entre ambas."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "tienda" / "pumaverso.db"))
    monkeypatch.setenv("SYNC_AUTOMATICA", "false")
    get_settings.cache_clear()

    central = TestClient(app_central)
    demo.cargar_central(central)
    red = Red(central)
    transporte = TransporteCentralHttp(
        "http://central", cliente=httpx.Client(transport=httpx.MockTransport(red))
    )
    app_tienda.dependency_overrides[obtener_transporte_central] = lambda: transporte

    with TestClient(app_tienda) as tienda:
        demo.cargar_existencias(tienda, 24)
        assert tienda.post("/sincronizacion/ejecutar").status_code == 200  # sube la recepción
        yield tienda, central, red

    app_tienda.dependency_overrides.clear()
    get_settings.cache_clear()


def _vender(tienda: TestClient, sku: str, cantidad: int) -> dict:
    login = tienda.post("/auth/login", json={"usuario_id": "cajero1", "pin": "1234"})
    sesion = {"Authorization": f"Bearer {login.json()['token']}"}
    venta = tienda.post(
        "/pos/ventas", headers=sesion, json={"items": [{"sku": sku, "cantidad": cantidad}]}
    )
    assert venta.status_code in (200, 201), venta.text
    return venta.json()


def _pendientes(tienda: TestClient) -> int:
    return tienda.get("/sincronizacion/estado").json()["pendientes"]


def _consecutivos_en_central(conn_central) -> list[int]:
    filas = conn_central.execute(
        """
        SELECT consecutivo FROM sincronizacion.eventos_recibidos
        WHERE sucursal_id = 'S001' AND caja_id = 'C01'
        ORDER BY consecutivo
        """
    ).fetchall()
    return [f["consecutivo"] for f in filas]


def _existencia_en_central(central: TestClient, conn_central, sku: str) -> int:
    central.post("/sincronizacion/procesar-cola")
    fila = conn_central.execute(
        "SELECT cantidad FROM inventario.existencias_sucursal WHERE sucursal_id = 'S001' "
        "AND sku = %s",
        (sku,),
    ).fetchone()
    return fila["cantidad"]


def test_la_caja_vende_sin_central_y_envia_la_venta_al_volver(sistema, conn_central):
    tienda, central, red = sistema

    red.cortada = True
    venta = _vender(tienda, REFRESCO, 2)
    fallo = tienda.post("/sincronizacion/ejecutar")

    assert venta["consecutivo"] == 2
    assert fallo.status_code == 503
    assert _pendientes(tienda) == 1
    assert _consecutivos_en_central(conn_central) == [1]

    red.cortada = False
    regreso = tienda.post("/sincronizacion/ejecutar")

    assert regreso.status_code == 200
    assert regreso.json()["eventos_enviados"] == 1
    assert _pendientes(tienda) == 0
    assert _consecutivos_en_central(conn_central) == [1, 2]
    assert _existencia_en_central(central, conn_central, REFRESCO) == 22


def test_si_se_pierde_la_confirmacion_la_venta_no_se_duplica(sistema, conn_central):
    tienda, central, red = sistema
    _vender(tienda, REFRESCO, 2)

    red.perder_respuesta_de_subida = True
    perdida = tienda.post("/sincronizacion/ejecutar")

    # Central sí la guardó, pero la tienda no lo sabe y la sigue teniendo pendiente.
    assert perdida.status_code == 503
    assert _consecutivos_en_central(conn_central) == [1, 2]
    assert _pendientes(tienda) == 1

    red.perder_respuesta_de_subida = False
    reintento = tienda.post("/sincronizacion/ejecutar")

    assert reintento.status_code == 200
    assert _pendientes(tienda) == 0
    assert _consecutivos_en_central(conn_central) == [1, 2]
    assert _existencia_en_central(central, conn_central, REFRESCO) == 22  # se descontó una vez


def test_varias_ventas_sin_conexion_llegan_completas_y_en_orden(sistema, conn_central):
    tienda, central, red = sistema

    red.cortada = True
    for _ in range(5):
        _vender(tienda, REFRESCO, 1)
    assert tienda.post("/sincronizacion/ejecutar").status_code == 503
    assert _pendientes(tienda) == 5

    red.cortada = False
    assert tienda.post("/sincronizacion/ejecutar").status_code == 200

    assert _consecutivos_en_central(conn_central) == [1, 2, 3, 4, 5, 6]
    assert _existencia_en_central(central, conn_central, REFRESCO) == 19

"""Script de datos de demostración (scripts/cargar_datos_demo.py)."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.main import app
from pumaverso_tienda.sincronizacion import (
    ActualizacionProducto,
    ActualizacionUsuario,
    PaqueteBajada,
    RespuestaSubida,
)
from pumaverso_tienda.usuarios import generar_hash_pin

RUTA_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "cargar_datos_demo.py"


def _cargar_script():
    especificacion = importlib.util.spec_from_file_location("cargar_datos_demo", RUTA_SCRIPT)
    modulo = importlib.util.module_from_spec(especificacion)
    especificacion.loader.exec_module(modulo)
    return modulo


demo = _cargar_script()


class CentralConDatosDemo:
    """Central de prueba que devuelve lo mismo que dejaría el script en uno real."""

    def __init__(self, *, con_productos: bool = True) -> None:
        ahora = datetime.now(UTC)
        self.bajada = PaqueteBajada(
            usuarios=tuple(
                ActualizacionUsuario(
                    usuario_id=u.usuario_id,
                    nombre=u.nombre,
                    rol=u.rol,
                    hash_pin=generar_hash_pin(u.pin),
                    activo=True,
                    actualizado_en=ahora,
                )
                for u in demo.USUARIOS
            ),
            productos=tuple(
                ActualizacionProducto(
                    sku=p.sku,
                    nombre=p.nombre,
                    precio_centavos=p.precio_centavos,
                    precio_actualizado_en=ahora,
                    costo_centavos=p.costo_centavos,
                    costo_actualizado_en=ahora,
                )
                for p in demo.PRODUCTOS
            )
            if con_productos
            else (),
        )

    def descargar_actualizaciones(self, sucursal_id, desde):
        return self.bajada

    def subir_eventos(self, sucursal_id, caja_id, eventos):
        return RespuestaSubida(ultimo_consecutivo_confirmado=eventos[-1].consecutivo)


@pytest.fixture
def tienda_con_central(tmp_path, monkeypatch):
    def preparar(central):
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "datos" / "pumaverso.db"))
        get_settings.cache_clear()
        app.dependency_overrides[obtener_transporte_central] = lambda: central
        return TestClient(app)

    yield preparar
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def test_deja_las_existencias_pedidas(tienda_con_central):
    with tienda_con_central(CentralConDatosDemo()) as tienda:
        recibidas = demo.cargar_existencias(tienda, 5)
        catalogo = tienda.get("/catalogo", params={"limite": 100}).json()

    assert recibidas == 5 * len(demo.PRODUCTOS)
    assert {p["existencia"] for p in catalogo} == {5}


def test_correrlo_dos_veces_no_duplica_existencias(tienda_con_central):
    with tienda_con_central(CentralConDatosDemo()) as tienda:
        demo.cargar_existencias(tienda, 5)
        segunda = demo.cargar_existencias(tienda, 5)
        catalogo = tienda.get("/catalogo", params={"limite": 100}).json()

    assert segunda == 0
    assert {p["existencia"] for p in catalogo} == {5}


def test_si_la_tienda_no_recibio_el_catalogo_lo_explica(tienda_con_central):
    with (
        tienda_con_central(CentralConDatosDemo(con_productos=False)) as tienda,
        pytest.raises(demo.ErrorDemo, match="CENTRAL_URL"),
    ):
        demo.cargar_existencias(tienda, 5)


def test_los_usuarios_de_la_demo_pueden_iniciar_sesion(tienda_con_central):
    with tienda_con_central(CentralConDatosDemo()) as tienda:
        demo.cargar_existencias(tienda, 1)
        for usuario in demo.USUARIOS:
            resp = tienda.post(
                "/auth/login", json={"usuario_id": usuario.usuario_id, "pin": usuario.pin}
            )
            assert resp.status_code == 200, usuario.usuario_id


def test_sin_servidores_termina_con_error_claro(capsys):
    codigo = demo.main(["--central", "http://127.0.0.1:9", "--tienda", "http://127.0.0.1:9"])

    assert codigo == 1
    assert "No se pudo conectar" in capsys.readouterr().err

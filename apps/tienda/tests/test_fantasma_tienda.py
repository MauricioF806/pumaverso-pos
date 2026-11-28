"""El gerente ve y cierra el inventario fantasma desde la tienda (decisiones 0009 a 0011)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pumaverso_contratos import LineaRecepcion

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.inventario import registrar_recepcion_proveedor
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.sincronizacion import (
    CredencialRechazadaError,
    ErrorSincronizacion,
    SospechaNoEncontradaError,
)
from pumaverso_tienda.usuarios import generar_hash_pin, guardar_usuario_local

AHORA = datetime.now(UTC).isoformat()
SOSPECHA = {
    "id": 7,
    "sucursal_id": "S001",
    "sku": "leche-1l",
    "nombre": "Leche entera 1 L",
    "estado": "pendiente_conteo",
    "franja_inicio": AHORA,
    "franja_fin": AHORA,
    "unidades_esperadas": 5.6,
    "unidades_vendidas": 0,
    "umbral_unidades": 0,
    "probabilidad": 0.0037,
    "existencia_registrada": 1499,
    "venta_perdida_estimada_centavos": 16800,
    "franjas_afectadas": 1,
    "detectada_en": AHORA,
    "escalada_en": None,
}


class CentralFantasma:
    def __init__(self, falla: Exception | None = None) -> None:
        self.falla = falla
        self.conteos: list[tuple[int, int, str]] = []
        self.consultas: list[str] = []

    def consultar_sospechas_fantasma(self, sucursal_id):
        if self.falla:
            raise self.falla
        self.consultas.append(sucursal_id)
        return {
            "sospechas": [SOSPECHA],
            "sugerencia_pedido": ["leche-1l"],
            "venta_perdida_estimada_centavos": 16800,
        }

    def registrar_conteo_fantasma(self, sospecha_id, cantidad_contada, usuario_id):
        if self.falla:
            raise self.falla
        self.conteos.append((sospecha_id, cantidad_contada, usuario_id))
        return {"sospecha_id": sospecha_id, "sku": "leche-1l", "diferencia": -1499}


@pytest.fixture
def tienda(tmp_path, monkeypatch):
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    get_settings.cache_clear()
    con = abrir_conexion(ruta)
    aplicar_migraciones(con)
    ahora = datetime.now(UTC)
    for usuario_id, rol, pin in (("cajero1", "cajero", "1234"), ("gerente1", "gerente", "9999")):
        guardar_usuario_local(
            con,
            usuario_id=usuario_id,
            nombre=usuario_id,
            rol=rol,
            hash_pin=generar_hash_pin(pin),
            actualizado_en=ahora,
        )
    guardar_producto(
        con,
        sku="leche-1l",
        nombre="Leche entera 1 L",
        precio_centavos=3000,
        precio_actualizado_en=ahora,
        costo_centavos=2200,
        costo_actualizado_en=ahora,
    )
    registrar_recepcion_proveedor(
        con,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="gerente1",
        proveedor_id="PROV",
        folio_documento="F-1",
        partidas=[LineaRecepcion(sku="leche-1l", cantidad=24, costo_unitario_centavos=2200)],
    )
    con.close()

    def preparar(central: CentralFantasma) -> TestClient:
        app.dependency_overrides[obtener_transporte_central] = lambda: central
        return TestClient(app)

    yield preparar
    app.dependency_overrides.clear()
    get_settings.cache_clear()


def _sesion(cliente, usuario_id="gerente1", pin="9999") -> dict[str, str]:
    token = cliente.post("/auth/login", json={"usuario_id": usuario_id, "pin": pin}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_el_gerente_ve_las_sospechas_de_su_sucursal_con_la_existencia_local(tienda):
    central = CentralFantasma()
    cliente = tienda(central)

    reporte = cliente.get("/inventario/fantasma", headers=_sesion(cliente))

    assert reporte.status_code == 200
    sospecha = reporte.json()["sospechas"][0]
    assert sospecha["nombre"] == "Leche entera 1 L"
    assert sospecha["existencia_local"] == 24
    assert reporte.json()["venta_perdida_estimada_centavos"] == 16800
    assert central.consultas == ["S001"]


def test_un_cajero_no_ve_el_reporte(tienda):
    cliente = tienda(CentralFantasma())

    respuesta = cliente.get("/inventario/fantasma", headers=_sesion(cliente, "cajero1", "1234"))

    assert respuesta.status_code == 403


def test_sin_sesion_no_hay_reporte(tienda):
    assert tienda(CentralFantasma()).get("/inventario/fantasma").status_code == 401


def test_el_conteo_se_envia_a_nombre_del_gerente_y_la_diferencia_es_contra_la_tienda(tienda):
    central = CentralFantasma()
    cliente = tienda(central)

    conteo = cliente.post(
        "/inventario/fantasma/7/conteo", json={"cantidad_contada": 3}, headers=_sesion(cliente)
    )

    assert conteo.status_code == 200
    assert conteo.json() == {
        "sospecha_id": 7,
        "sku": "leche-1l",
        "cantidad_contada": 3,
        "existencia_local": 24,
        "diferencia": -21,  # no -1499: manda lo que tiene la tienda
    }
    assert central.conteos == [(7, 3, "gerente1")]


@pytest.mark.parametrize(
    ("falla", "estado", "texto"),
    [
        (ErrorSincronizacion("sin red"), 503, "Sin conexión con central"),
        (CredencialRechazadaError("Revisa LLAVE_CENTRAL"), 502, "LLAVE_CENTRAL"),
        (SospechaNoEncontradaError("no"), 404, "ya no está abierta"),
    ],
)
def test_si_central_falla_se_explica(tienda, falla, estado, texto):
    cliente = tienda(CentralFantasma(falla))
    sesion = _sesion(cliente)

    reporte = cliente.get("/inventario/fantasma", headers=sesion)
    conteo = cliente.post(
        "/inventario/fantasma/7/conteo", json={"cantidad_contada": 0}, headers=sesion
    )

    assert reporte.status_code == estado
    assert conteo.status_code == estado
    assert texto in reporte.json()["detail"]

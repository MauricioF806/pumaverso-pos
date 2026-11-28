"""Límite de tasa por sucursal (decisión 0012)."""

import os

import pytest
from fastapi.testclient import TestClient

from pumaverso_central import main
from pumaverso_central.config import get_central_settings
from pumaverso_central.limite import LimitadorPorSucursal
from pumaverso_central.main import app

ADMIN = {"Authorization": f"Bearer {os.environ['CENTRAL_LLAVE_ADMIN']}"}
LLAVES = {
    "S001": "llave-de-la-sucursal-s001-para-pruebas",
    "S002": "llave-de-la-sucursal-s002-para-pruebas",
}


class Reloj:
    def __init__(self) -> None:
        self.ahora = 1000.0

    def __call__(self) -> float:
        return self.ahora


def test_deja_pasar_una_rafaga_y_luego_pide_esperar():
    reloj = Reloj()
    limitador = LimitadorPorSucursal(reloj)

    pasan = [limitador.intentar("S001", por_minuto=60, rafaga=3) for _ in range(3)]
    cuarta = limitador.intentar("S001", por_minuto=60, rafaga=3)

    assert pasan == [None, None, None]
    assert cuarta == pytest.approx(1.0)  # 60 por minuto: una ficha por segundo


def test_las_fichas_se_recuperan_con_el_tiempo():
    reloj = Reloj()
    limitador = LimitadorPorSucursal(reloj)
    for _ in range(3):
        limitador.intentar("S001", por_minuto=60, rafaga=3)

    reloj.ahora += 2
    assert limitador.intentar("S001", por_minuto=60, rafaga=3) is None
    assert limitador.intentar("S001", por_minuto=60, rafaga=3) is None
    assert limitador.intentar("S001", por_minuto=60, rafaga=3) is not None


def test_cada_sucursal_tiene_su_propia_cubeta():
    limitador = LimitadorPorSucursal(Reloj())
    limitador.intentar("S001", por_minuto=60, rafaga=1)

    assert limitador.intentar("S001", por_minuto=60, rafaga=1) is not None
    assert limitador.intentar("S002", por_minuto=60, rafaga=1) is None


@pytest.fixture
def central(conn_central, monkeypatch):
    cliente = TestClient(app)
    for sucursal, llave in LLAVES.items():
        cliente.put(f"/sucursales/{sucursal}/llave", headers=ADMIN, json={"llave": llave})
    estricta = get_central_settings().model_copy(
        update={"limite_por_minuto": 60, "limite_rafaga": 2}
    )
    monkeypatch.setattr(main, "get_central_settings", lambda: estricta)
    return cliente


def _bajar(cliente, sucursal: str, encabezados: dict[str, str] | None = None):
    return cliente.get(
        "/sincronizacion/actualizaciones",
        params={"sucursal_id": sucursal},
        headers=encabezados or {"Authorization": f"Bearer {LLAVES[sucursal]}"},
    )


def test_la_api_responde_429_con_cuanto_esperar(central):
    estados = [_bajar(central, "S001").status_code for _ in range(2)]
    tercera = _bajar(central, "S001")

    assert estados == [200, 200]
    assert tercera.status_code == 429
    assert tercera.headers["retry-after"] == "1"
    assert "S001" in tercera.json()["detail"]


def test_una_sucursal_que_pide_mucho_no_frena_a_las_demas(central):
    for _ in range(3):
        _bajar(central, "S001")

    assert _bajar(central, "S002").status_code == 200


def test_la_llave_de_administracion_no_tiene_limite(central):
    estados = {_bajar(central, "S001", ADMIN).status_code for _ in range(5)}

    assert estados == {200}


def test_con_limite_cero_no_hay_limite(central, monkeypatch):
    sin_limite = get_central_settings().model_copy(update={"limite_por_minuto": 0})
    monkeypatch.setattr(main, "get_central_settings", lambda: sin_limite)

    assert {_bajar(central, "S001").status_code for _ in range(5)} == {200}

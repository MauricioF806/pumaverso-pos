import pytest
from fastapi.testclient import TestClient

from pumaverso_tienda.config import Settings, get_settings
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, version_actual
from pumaverso_tienda.persistencia.migraciones import MIGRACIONES


@pytest.fixture
def configuracion_temporal(tmp_path, monkeypatch):
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    get_settings.cache_clear()
    yield ruta
    get_settings.cache_clear()


def test_al_arrancar_se_crea_la_base_de_datos_con_sus_migraciones(configuracion_temporal):
    with TestClient(app) as cliente:
        assert cliente.get("/health").status_code == 200

    conexion = abrir_conexion(configuracion_temporal)
    assert version_actual(conexion) == len(MIGRACIONES)
    conexion.close()


def test_configuracion_lee_sucursal_y_caja_del_entorno(monkeypatch):
    monkeypatch.setenv("SUCURSAL_ID", "S214")
    monkeypatch.setenv("CAJA_ID", "C01")

    configuracion = Settings(_env_file=None)

    assert configuracion.sucursal_id == "S214"
    assert configuracion.caja_id == "C01"


@pytest.mark.parametrize("valor", ["", "con espacios", "a" * 33])
def test_configuracion_rechaza_identificadores_invalidos(monkeypatch, valor):
    monkeypatch.setenv("SUCURSAL_ID", valor)

    with pytest.raises(ValueError):
        Settings(_env_file=None)

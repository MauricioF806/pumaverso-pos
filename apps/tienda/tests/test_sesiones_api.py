"""Revisión, falla 2: la identidad se comprueba con PIN y no solo se declara."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.eventos import listar_eventos
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.usuarios import (
    DURACION_SESION,
    SesionInvalidaError,
    generar_hash_pin,
    guardar_usuario_local,
    iniciar_sesion,
    usuario_de_sesion,
)

VENTA = {"items": [{"sku": "coca-600", "cantidad": 1}]}


@pytest.fixture
def tienda(tmp_path, monkeypatch):
    """Tienda con un cajero, un segundo cajero, un gerente y un producto."""
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    get_settings.cache_clear()

    con = abrir_conexion(ruta)
    aplicar_migraciones(con)
    ahora = datetime.now(UTC)
    for usuario_id, rol, pin in (
        ("cajero-1", "cajero", "1234"),
        ("cajero-2", "cajero", "5678"),
        ("gerente-1", "gerente", "9999"),
    ):
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
        sku="coca-600",
        nombre="Refresco Cola 600ml",
        precio_centavos=1800,
        precio_actualizado_en=ahora,
        costo_centavos=1100,
        costo_actualizado_en=ahora,
    )

    with TestClient(app) as cliente:
        yield cliente, con
    con.close()
    get_settings.cache_clear()


def _sesion(cliente, usuario_id="cajero-1", pin="1234") -> dict[str, str]:
    resp = cliente.post("/auth/login", json={"usuario_id": usuario_id, "pin": pin})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_sin_sesion_no_se_puede_vender(tienda):
    cliente, _ = tienda

    assert cliente.post("/pos/ventas", json=VENTA).status_code == 401


def test_un_token_inventado_no_sirve(tienda):
    cliente, _ = tienda

    resp = cliente.post("/pos/ventas", json=VENTA, headers={"Authorization": "Bearer inventado"})

    assert resp.status_code == 401


def test_la_venta_queda_a_nombre_del_usuario_de_la_sesion(tienda):
    cliente, con = tienda
    # Aunque la petición diga otro usuario, se ignora: manda la sesión.
    resp = cliente.post(
        "/pos/ventas", json={**VENTA, "usuario_id": "gerente-1"}, headers=_sesion(cliente)
    )

    assert resp.status_code == 201
    assert listar_eventos(con, "S001", "C01")[-1].usuario_id == "cajero-1"


def test_pin_incorrecto_no_abre_sesion(tienda):
    cliente, _ = tienda

    resp = cliente.post("/auth/login", json={"usuario_id": "cajero-1", "pin": "0000"})

    assert resp.status_code == 401


def test_cancelar_requiere_pin_correcto_del_gerente(tienda):
    cliente, _ = tienda
    sesion = _sesion(cliente)
    venta = cliente.post("/pos/ventas", json=VENTA, headers=sesion).json()

    resp = cliente.post(
        f"/pos/ventas/{venta['consecutivo']}/cancelar",
        json={"motivo": "Devolución", "aprobador_id": "gerente-1", "aprobador_pin": "0000"},
        headers=sesion,
    )

    assert resp.status_code == 403


@pytest.mark.parametrize(
    ("aprobador_id", "pin"),
    [("jefe-fantasma", "1111"), ("cajero-2", "5678"), ("cajero-1", "1234")],
    ids=["no-existe", "rol-cajero", "se-aprueba-a-si-mismo"],
)
def test_cancelar_rechaza_aprobadores_invalidos(tienda, aprobador_id, pin):
    cliente, _ = tienda
    sesion = _sesion(cliente)
    venta = cliente.post("/pos/ventas", json=VENTA, headers=sesion).json()

    resp = cliente.post(
        f"/pos/ventas/{venta['consecutivo']}/cancelar",
        json={"motivo": "Devolución", "aprobador_id": aprobador_id, "aprobador_pin": pin},
        headers=sesion,
    )

    assert resp.status_code == 403


def test_cancelar_con_gerente_y_su_pin_funciona(tienda):
    cliente, con = tienda
    sesion = _sesion(cliente)
    venta = cliente.post("/pos/ventas", json=VENTA, headers=sesion).json()

    resp = cliente.post(
        f"/pos/ventas/{venta['consecutivo']}/cancelar",
        json={"motivo": "Devolución", "aprobador_id": "gerente-1", "aprobador_pin": "9999"},
        headers=sesion,
    )

    assert resp.status_code == 201
    cancelacion = listar_eventos(con, "S001", "C01")[-1]
    assert cancelacion.usuario_id == "cajero-1"
    assert cancelacion.contenido["aprobado_por"] == "gerente-1"


def test_ajuste_sin_aprobacion_valida_se_rechaza(tienda):
    cliente, _ = tienda

    resp = cliente.post(
        "/inventario/ajustes",
        json={
            "sku": "coca-600",
            "diferencia_cantidad": -10,
            "motivo": "Conteo",
            "aprobador_id": "jefe-fantasma",
            "aprobador_pin": "1111",
        },
        headers=_sesion(cliente),
    )

    assert resp.status_code == 403


def test_la_sesion_deja_de_servir_al_dar_de_baja_al_usuario(tienda):
    cliente, con = tienda
    sesion = _sesion(cliente)
    guardar_usuario_local(
        con,
        usuario_id="cajero-1",
        nombre="cajero-1",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        activo=False,
        actualizado_en=datetime.now(UTC),
    )

    resp = cliente.post(
        "/inventario/mermas", json={"sku": "coca-600", "cantidad": 1, "motivo": "x"}, headers=sesion
    )

    assert resp.status_code == 403


def test_cerrar_sesion_invalida_el_token(tienda):
    cliente, _ = tienda
    sesion = _sesion(cliente)

    assert cliente.post("/auth/logout", headers=sesion).status_code == 204
    assert cliente.post("/pos/ventas", json=VENTA, headers=sesion).status_code == 401


def test_la_sesion_expira(tienda):
    _, con = tienda
    inicio = datetime.now(UTC)
    sesion = iniciar_sesion(con, "cajero-1", "1234", ahora=inicio)

    assert usuario_de_sesion(con, sesion.token, ahora=inicio + timedelta(hours=1))
    with pytest.raises(SesionInvalidaError):
        usuario_de_sesion(con, sesion.token, ahora=inicio + DURACION_SESION)


def test_no_se_guarda_el_token_en_la_base(tienda):
    _, con = tienda
    sesion = iniciar_sesion(con, "cajero-1", "1234")

    guardados = [fila[0] for fila in con.execute("SELECT token_hash FROM sesiones")]
    assert sesion.token not in guardados

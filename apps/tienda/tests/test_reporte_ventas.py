"""Reporte de ventas para el gerente (decisión 0012)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pumaverso_contratos import LineaRecepcion

from pumaverso_tienda.catalogo import guardar_producto
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.inventario import registrar_recepcion_proveedor
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.pos import (
    ItemVentaSolicitado,
    cancelar_venta,
    registrar_venta,
    reporte_de_ventas,
)
from pumaverso_tienda.usuarios import generar_hash_pin, guardar_usuario_local

AHORA = datetime.now(UTC).replace(microsecond=0)
PRODUCTOS = {"leche": ("Leche entera 1 L", 3000), "pan": ("Pan blanco", 4500)}


@pytest.fixture
def tienda(tmp_path, monkeypatch):
    """Una venta de ayer, tres de hoy (una cancelada) por dos cajeros."""
    ruta = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta))
    get_settings.cache_clear()
    con = abrir_conexion(ruta)
    aplicar_migraciones(con)
    inicio = AHORA - timedelta(hours=6)
    for usuario_id, nombre, rol in (
        ("cajero1", "Ana López", "cajero"),
        ("cajero2", "Luis Pérez", "cajero"),
        ("gerente1", "Rosa Díaz", "gerente"),
    ):
        guardar_usuario_local(
            con,
            usuario_id=usuario_id,
            nombre=nombre,
            rol=rol,
            hash_pin=generar_hash_pin("9999"),
            actualizado_en=inicio,
        )
    for sku, (nombre, precio) in PRODUCTOS.items():
        guardar_producto(
            con,
            sku=sku,
            nombre=nombre,
            precio_centavos=precio,
            precio_actualizado_en=inicio,
            costo_centavos=precio // 2,
            costo_actualizado_en=inicio,
        )
    registrar_recepcion_proveedor(
        con,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="gerente1",
        proveedor_id="PROV",
        folio_documento="F-1",
        partidas=[
            LineaRecepcion(sku=sku, cantidad=50, costo_unitario_centavos=100) for sku in PRODUCTOS
        ],
        ahora=inicio,
    )

    def vender(usuario, horas_atras, items):
        return registrar_venta(
            con,
            sucursal_id="S001",
            caja_id="C01",
            usuario_id=usuario,
            items=[ItemVentaSolicitado(sku=s, cantidad=c) for s, c in items],
            ahora=AHORA - timedelta(hours=horas_atras),
        )

    vender("cajero1", 5, [("pan", 9)])  # fuera del rango
    vender("cajero1", 3, [("leche", 2)])  # $60
    vender("cajero2", 2, [("leche", 1), ("pan", 1)])  # $75
    cancelada = vender("cajero1", 1, [("pan", 2)])  # $90, se cancela
    cancelar_venta(
        con,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="cajero1",
        consecutivo_original=cancelada.evento.consecutivo,
        motivo="Devolución",
        aprobado_por="gerente1",
        ahora=AHORA - timedelta(minutes=50),
    )
    yield con
    con.close()
    get_settings.cache_clear()


DESDE = AHORA - timedelta(hours=4)
HASTA = AHORA + timedelta(hours=1)


def test_suma_solo_lo_vendido_en_el_rango_y_aparta_lo_cancelado(tienda):
    reporte = reporte_de_ventas(tienda, "S001", "C01", desde=DESDE, hasta=HASTA)

    assert reporte.ventas == 2
    assert reporte.total_centavos == 6000 + 7500
    assert reporte.piezas == 4
    assert reporte.ticket_promedio_centavos == 6750
    assert (reporte.canceladas, reporte.canceladas_centavos) == (1, 9000)


def test_desglosa_por_cajero_y_productos(tienda):
    reporte = reporte_de_ventas(tienda, "S001", "C01", desde=DESDE, hasta=HASTA)

    assert [(c.nombre, c.ventas, c.total_centavos) for c in reporte.por_cajero] == [
        ("Luis Pérez", 1, 7500),
        ("Ana López", 1, 6000),
    ]
    assert [(p.nombre, p.piezas) for p in reporte.productos_mas_vendidos] == [
        ("Leche entera 1 L", 3),
        ("Pan blanco", 1),
    ]


def test_sin_ventas_en_el_rango_todo_en_cero(tienda):
    reporte = reporte_de_ventas(
        tienda, "S001", "C01", desde=AHORA + timedelta(hours=2), hasta=AHORA + timedelta(hours=3)
    )

    assert (reporte.ventas, reporte.total_centavos, reporte.ticket_promedio_centavos) == (0, 0, 0)
    assert reporte.por_cajero == []


@pytest.mark.parametrize(
    ("desde", "hasta", "texto"),
    [
        (HASTA, DESDE, "posterior"),
        (DESDE, DESDE + timedelta(days=40), "31 días"),
        (DESDE.replace(tzinfo=None), HASTA, "zona horaria"),
    ],
)
def test_rangos_invalidos_se_explican(tienda, desde, hasta, texto):
    with pytest.raises(ValueError, match=texto):
        reporte_de_ventas(tienda, "S001", "C01", desde=desde, hasta=hasta)


def _sesion(cliente, usuario_id):
    token = cliente.post("/auth/login", json={"usuario_id": usuario_id, "pin": "9999"}).json()[
        "token"
    ]
    return {"Authorization": f"Bearer {token}"}


def test_la_api_es_solo_para_gerente_o_supervisor(tienda):
    cliente = TestClient(app)
    rango = {"desde": DESDE.isoformat(), "hasta": HASTA.isoformat()}

    gerente = cliente.get("/reportes/ventas", params=rango, headers=_sesion(cliente, "gerente1"))
    cajero = cliente.get("/reportes/ventas", params=rango, headers=_sesion(cliente, "cajero1"))

    assert gerente.status_code == 200
    assert gerente.json()["total_centavos"] == 13500
    assert gerente.json()["por_cajero"][0]["nombre"] == "Luis Pérez"
    assert cajero.status_code == 403


def test_la_api_rechaza_un_rango_sin_zona_horaria(tienda):
    cliente = TestClient(app)

    respuesta = cliente.get(
        "/reportes/ventas",
        params={"desde": "2026-11-27T00:00:00", "hasta": "2026-11-28T00:00:00"},
        headers=_sesion(cliente, "gerente1"),
    )

    assert respuesta.status_code == 422
    assert "zona horaria" in respuesta.json()["detail"]

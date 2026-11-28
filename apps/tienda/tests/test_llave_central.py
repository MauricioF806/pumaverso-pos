"""La tienda se identifica ante central con la llave de su sucursal (decisión 0010)."""

import httpx
import pytest

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.sincronizacion import (
    CredencialRechazadaError,
    TrabajadorSincronizacion,
    TransporteCentralHttp,
)

LLAVE = "llave-de-la-sucursal-s001-para-pruebas"


class CentralQueAnota:
    """Responde como central y guarda los encabezados que recibió."""

    def __init__(self, estado: int = 200) -> None:
        self.estado = estado
        self.encabezados: list[httpx.Headers] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.encabezados.append(peticion.headers)
        if self.estado != 200:
            return httpx.Response(self.estado, json={"detail": "La llave no es válida."})
        if peticion.url.path == "/sincronizacion/actualizaciones":
            return httpx.Response(200, json={"usuarios": [], "productos": []})
        return httpx.Response(200, json={"ultimo_consecutivo_confirmado": 0})


def _transporte(central: CentralQueAnota, llave: str | None = LLAVE) -> TransporteCentralHttp:
    return TransporteCentralHttp(
        "http://central",
        llave=llave,
        cliente=httpx.Client(transport=httpx.MockTransport(central)),
    )


def test_envia_la_llave_al_bajar_y_al_subir():
    central = CentralQueAnota()
    transporte = _transporte(central)

    transporte.descargar_actualizaciones("S001", None)
    transporte.subir_eventos("S001", "C01", [])

    assert [h["authorization"] for h in central.encabezados] == [f"Bearer {LLAVE}"] * 2


def test_sin_llave_configurada_no_envia_encabezado():
    central = CentralQueAnota()

    _transporte(central, llave=None).descargar_actualizaciones("S001", None)

    assert "authorization" not in central.encabezados[0]


@pytest.mark.parametrize("estado", [401, 403])
def test_si_central_rechaza_la_llave_lo_explica(estado):
    transporte = _transporte(CentralQueAnota(estado))

    with pytest.raises(CredencialRechazadaError, match="LLAVE_CENTRAL"):
        transporte.descargar_actualizaciones("S001", None)
    with pytest.raises(CredencialRechazadaError, match="LLAVE_CENTRAL"):
        transporte.subir_eventos("S001", "C01", [])


def test_el_trabajador_lo_marca_como_conflicto_y_no_insiste_rapido(ruta_bd):
    con = abrir_conexion(ruta_bd)
    aplicar_migraciones(con)
    con.close()
    trabajador = TrabajadorSincronizacion(
        abrir_conexion=lambda: abrir_conexion(ruta_bd),
        crear_transporte=lambda: _transporte(CentralQueAnota(401)),
        sucursal_id="S001",
        caja_id="C01",
        limite_lote=100,
        intervalo_segundos=60.0,
        espera_maxima_segundos=300.0,
    )

    espera = trabajador.ejecutar_ciclo()

    assert trabajador.estado.en_conflicto is True
    assert "LLAVE_CENTRAL" in trabajador.estado.ultimo_error
    assert espera == 60.0


def test_la_tienda_toma_la_llave_de_su_configuracion(monkeypatch):
    monkeypatch.setenv("LLAVE_CENTRAL", LLAVE)
    get_settings.cache_clear()
    try:
        transporte = obtener_transporte_central()
    finally:
        get_settings.cache_clear()

    assert transporte._encabezados == {"Authorization": f"Bearer {LLAVE}"}


def test_el_reporte_de_fantasma_tambien_lleva_la_llave():
    central = CentralQueAnota()

    _transporte(central).consultar_sospechas_fantasma("S001")

    assert central.encabezados[0]["authorization"] == f"Bearer {LLAVE}"


def test_un_conteo_de_una_sospecha_que_no_existe_se_distingue():
    from pumaverso_tienda.sincronizacion import SospechaNoEncontradaError

    def responde_404(peticion: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "No hay una sospecha abierta con id 9."})

    transporte = TransporteCentralHttp(
        "http://central",
        llave=LLAVE,
        cliente=httpx.Client(transport=httpx.MockTransport(responde_404)),
    )

    with pytest.raises(SospechaNoEncontradaError):
        transporte.registrar_conteo_fantasma(9, 0, "gerente1")

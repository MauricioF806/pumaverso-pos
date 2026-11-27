import random
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pumaverso_contratos import EventoContrato

from pumaverso_tienda.api.dependencias import obtener_transporte_central
from pumaverso_tienda.catalogo import validar_producto_para_venta
from pumaverso_tienda.config import get_settings
from pumaverso_tienda.main import app
from pumaverso_tienda.persistencia import abrir_conexion, aplicar_migraciones
from pumaverso_tienda.pos import ItemVentaSolicitado, RelojAtrasadoError, registrar_venta
from pumaverso_tienda.sincronizacion import (
    ActualizacionProducto,
    ActualizacionUsuario,
    ConflictoSincronizacionError,
    ErrorSincronizacion,
    PaqueteBajada,
    RespuestaSubida,
    calcular_espera_reintento,
    estado_sincronizacion_caja,
    sincronizar_nodo,
)
from pumaverso_tienda.usuarios import (
    UsuarioInactivoError,
    autenticar_usuario,
    generar_hash_pin,
    guardar_usuario_local,
)

BASE = datetime(2026, 11, 25, 14, 0, tzinfo=UTC)


class TransporteFalso:
    def __init__(
        self,
        *,
        fallar: bool = False,
        conflicto_al_subir: bool = False,
        bajada: PaqueteBajada | None = None,
    ) -> None:
        self.fallar = fallar
        self.conflicto_al_subir = conflicto_al_subir
        self.bajada = bajada or PaqueteBajada()
        self.recibidos: list[EventoContrato] = []

    def subir_eventos(
        self, sucursal_id: str, caja_id: str, eventos: list[EventoContrato]
    ) -> RespuestaSubida:
        if self.fallar:
            raise ErrorSincronizacion("Central sin conexión")
        if self.conflicto_al_subir:
            raise ConflictoSincronizacionError("Conflicto de hash en S001/C01#1.")
        self.recibidos.extend(eventos)
        return RespuestaSubida(ultimo_consecutivo_confirmado=eventos[-1].consecutivo)

    def descargar_actualizaciones(self, sucursal_id: str, desde: datetime | None) -> PaqueteBajada:
        if self.fallar:
            raise ErrorSincronizacion("Central sin conexión")
        return self.bajada


def test_backoff_exponencial_con_jitter_respeta_cotas():
    rng = random.Random(42)
    espera_1 = calcular_espera_reintento(1, base_segundos=1.0, max_segundos=30.0, rng=rng)
    espera_4 = calcular_espera_reintento(4, base_segundos=1.0, max_segundos=30.0, rng=rng)
    espera_10 = calcular_espera_reintento(10, base_segundos=1.0, max_segundos=30.0, rng=rng)

    assert 0.0 <= espera_1 <= 1.0
    assert 0.0 <= espera_4 <= 8.0
    assert 0.0 <= espera_10 <= 30.0


def test_flujo_completo_api_offline_y_sincronizacion(tmp_path, monkeypatch):
    ruta_db = tmp_path / "datos" / "pumaverso.db"
    monkeypatch.setenv("DATABASE_PATH", str(ruta_db))
    monkeypatch.setenv("SUCURSAL_ID", "S001")
    monkeypatch.setenv("CAJA_ID", "C01")
    get_settings.cache_clear()

    # Sembramos un cajero inicial en la base local.
    con = abrir_conexion(ruta_db)
    aplicar_migraciones(con)
    guardar_usuario_local(
        con,
        usuario_id="cajero-1",
        nombre="Ana López",
        rol="cajero",
        hash_pin=generar_hash_pin("1234"),
        actualizado_en=datetime.now(UTC),
    )
    con.close()

    transporte_offline = TransporteFalso(fallar=True)
    app.dependency_overrides[obtener_transporte_central] = lambda: transporte_offline

    try:
        with TestClient(app) as cliente:
            # 1. Login local funciona sin conexión a central.
            resp_login = cliente.post("/auth/login", json={"usuario_id": "cajero-1", "pin": "1234"})
            assert resp_login.status_code == 200
            sesion = {"Authorization": f"Bearer {resp_login.json()['token']}"}

            # 2. Bajada inicial de catálogo desde central.
            transporte_online = TransporteFalso(
                bajada=PaqueteBajada(
                    productos=(
                        ActualizacionProducto(
                            sku="coca-600",
                            nombre="Refresco Cola 600ml",
                            precio_centavos=1800,
                            precio_actualizado_en=datetime.now(UTC),
                            costo_centavos=1100,
                            costo_actualizado_en=datetime.now(UTC),
                        ),
                    )
                )
            )
            app.dependency_overrides[obtener_transporte_central] = lambda: transporte_online
            resp_sync_1 = cliente.post("/sincronizacion/ejecutar")
            assert resp_sync_1.status_code == 200
            assert resp_sync_1.json()["productos_actualizados"] == 1

            # 3. Cae la red; la sucursal recibe mercancía y cobra una venta offline.
            app.dependency_overrides[obtener_transporte_central] = lambda: transporte_offline
            resp_rec = cliente.post(
                "/inventario/recepciones",
                headers=sesion,
                json={
                    "proveedor_id": "PROV01",
                    "folio_documento": "FAC-501",
                    "partidas": [
                        {"sku": "coca-600", "cantidad": 10, "costo_unitario_centavos": 1100}
                    ],
                },
            )
            assert resp_rec.status_code == 201

            resp_venta = cliente.post(
                "/pos/ventas",
                headers=sesion,
                json={"items": [{"sku": "coca-600", "cantidad": 2}]},
            )
            assert resp_venta.status_code == 201
            assert resp_venta.json()["total_centavos"] == 3600

            # Sin sesión no se puede operar.
            sin_sesion = cliente.post(
                "/pos/ventas", json={"items": [{"sku": "coca-600", "cantidad": 1}]}
            )
            assert sin_sesion.status_code == 401

            # Intentar sincronizar sin red devuelve 503 sin perder los 2 eventos pendientes.
            assert cliente.post("/sincronizacion/ejecutar").status_code == 503
            estado = cliente.get("/sincronizacion/estado").json()
            assert estado["pendientes"] == 2

            # 4. Regresa la conexión y sube los 2 eventos con confirmación idempotente.
            app.dependency_overrides[obtener_transporte_central] = lambda: transporte_online
            resp_sync_2 = cliente.post("/sincronizacion/ejecutar").json()
            assert resp_sync_2["eventos_enviados"] == 2
            assert resp_sync_2["ultimo_consecutivo_confirmado"] == 2
            assert len(transporte_online.recibidos) == 2

            estado_final = cliente.get("/sincronizacion/estado").json()
            assert estado_final["pendientes"] == 0
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_bajada_de_usuario_revocado_bloquea_login_inmediato(conexion):
    guardar_usuario_local(
        conexion,
        usuario_id="cajero-2",
        nombre="Luis Pérez",
        rol="cajero",
        hash_pin=generar_hash_pin("5678"),
        activo=True,
        actualizado_en=BASE,
    )
    transporte = TransporteFalso(
        bajada=PaqueteBajada(
            usuarios=(
                ActualizacionUsuario(
                    usuario_id="cajero-2",
                    nombre="Luis Pérez",
                    rol="cajero",
                    hash_pin=generar_hash_pin("5678"),
                    activo=False,
                    actualizado_en=BASE,
                ),
            )
        )
    )
    res = sincronizar_nodo(
        conexion, sucursal_id="S001", caja_id="C01", transporte=transporte, ahora=BASE
    )
    assert res.usuarios_actualizados == 1
    assert estado_sincronizacion_caja(conexion, "S001", "C01")["pendientes"] == 0
    with pytest.raises(UsuarioInactivoError):
        autenticar_usuario(conexion, "cajero-2", "5678", ahora=BASE)


# --- Revisión, falla 1: la vigencia se cuenta desde que la tienda recibe los datos ---


def _bajada_editada_hace(dias: int) -> PaqueteBajada:
    """Datos que central editó por última vez hace ``dias`` días."""
    editado = BASE - timedelta(days=dias)
    return PaqueteBajada(
        usuarios=(
            ActualizacionUsuario(
                usuario_id="cajero-1",
                nombre="Ana López",
                rol="cajero",
                hash_pin=generar_hash_pin("1234"),
                activo=True,
                actualizado_en=editado,
            ),
        ),
        productos=(
            ActualizacionProducto(
                sku="coca-600",
                nombre="Refresco Cola 600ml",
                precio_centavos=1800,
                precio_actualizado_en=editado,
                costo_centavos=1100,
                costo_actualizado_en=editado,
            ),
        ),
    )


def test_datos_editados_hace_dias_sirven_justo_despues_de_sincronizar(conexion):
    sincronizar_nodo(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        transporte=TransporteFalso(bajada=_bajada_editada_hace(40)),
        ahora=BASE,
    )
    un_momento_despues = BASE + timedelta(seconds=1)

    assert autenticar_usuario(conexion, "cajero-1", "1234", ahora=un_momento_despues)
    validacion = validar_producto_para_venta(conexion, "coca-600", ahora=un_momento_despues)
    assert validacion.costo_requiere_revision is False
    venta = registrar_venta(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=un_momento_despues,
    )
    assert venta.total_centavos == 1800


def test_la_vigencia_vence_si_la_tienda_deja_de_sincronizar(conexion):
    from pumaverso_tienda.usuarios import CredencialesExpiradasError

    sincronizar_nodo(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        transporte=TransporteFalso(bajada=_bajada_editada_hace(0)),
        ahora=BASE,
    )

    with pytest.raises(CredencialesExpiradasError):
        autenticar_usuario(conexion, "cajero-1", "1234", ahora=BASE + timedelta(hours=25))


def test_una_bajada_sin_eventos_tambien_cuenta_para_revisar_el_reloj(conexion):
    sincronizar_nodo(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        transporte=TransporteFalso(bajada=_bajada_editada_hace(0)),
        ahora=BASE,
    )

    with pytest.raises(RelojAtrasadoError):
        registrar_venta(
            conexion,
            sucursal_id="S001",
            caja_id="C01",
            usuario_id="cajero-1",
            items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
            ahora=BASE - timedelta(minutes=1),
        )


# --- Revisión, falla 3: un rechazo de central no bloquea la bajada ----------------


def test_conflicto_al_subir_no_impide_recibir_bajas(conexion):
    sincronizar_nodo(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        transporte=TransporteFalso(bajada=_bajada_editada_hace(0)),
        ahora=BASE,
    )
    registrar_venta(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(minutes=1),
    )
    baja = PaqueteBajada(
        usuarios=(
            ActualizacionUsuario(
                usuario_id="cajero-1",
                nombre="Ana López",
                rol="cajero",
                hash_pin=generar_hash_pin("1234"),
                activo=False,
                actualizado_en=BASE,
            ),
        )
    )

    with pytest.raises(ConflictoSincronizacionError):
        sincronizar_nodo(
            conexion,
            sucursal_id="S001",
            caja_id="C01",
            transporte=TransporteFalso(conflicto_al_subir=True, bajada=baja),
            ahora=BASE + timedelta(minutes=2),
        )

    with pytest.raises(UsuarioInactivoError):
        autenticar_usuario(conexion, "cajero-1", "1234", ahora=BASE + timedelta(minutes=3))
    assert estado_sincronizacion_caja(conexion, "S001", "C01")["pendientes"] == 1


def test_central_confirma_mas_eventos_de_los_que_hay_es_conflicto(conexion):
    class CentralConMasEventos(TransporteFalso):
        def subir_eventos(self, sucursal_id, caja_id, eventos):
            return RespuestaSubida(ultimo_consecutivo_confirmado=50)

    sincronizar_nodo(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        transporte=TransporteFalso(bajada=_bajada_editada_hace(0)),
        ahora=BASE,
    )
    registrar_venta(
        conexion,
        sucursal_id="S001",
        caja_id="C01",
        usuario_id="cajero-1",
        items=[ItemVentaSolicitado(sku="coca-600", cantidad=1)],
        ahora=BASE + timedelta(minutes=1),
    )

    with pytest.raises(ConflictoSincronizacionError, match="más eventos"):
        sincronizar_nodo(
            conexion,
            sucursal_id="S001",
            caja_id="C01",
            transporte=CentralConMasEventos(),
            ahora=BASE + timedelta(minutes=2),
        )

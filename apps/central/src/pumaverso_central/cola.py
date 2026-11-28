"""Ingesta idempotente de eventos y procesamiento asíncrono en PostgreSQL."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb
from pumaverso_contratos import (
    VERSION_ESQUEMA_ACTUAL,
    ContenidoAjuste,
    ContenidoCancelacion,
    ContenidoMerma,
    ContenidoRecepcionProveedor,
    ContenidoTraspaso,
    ContenidoVenta,
    EventoContrato,
    TipoEvento,
    validar_contenido_por_tipo,
)

from pumaverso_central.fantasma import resolver_por_ajuste

LONGITUD_MAXIMA_ERROR = 500

# Candado de PostgreSQL (pg_advisory_xact_lock) para aplicar la cola de uno en uno.
CANDADO_COLA = 7_301_202_608

# Errores que dependen del momento y no del evento: el lote se repite, no se aparta.
ERRORES_PASAJEROS = (
    psycopg.errors.DeadlockDetected,
    psycopg.errors.SerializationFailure,
    psycopg.errors.LockNotAvailable,
)


class ConflictoHashEventoError(ValueError):
    """Llegó un consecutivo ya registrado pero con distinto hash de contenido."""


class EventoInvalidoError(ValueError):
    """El evento no cumple el contrato: contenido, versión o hash incorrectos."""


@dataclass(frozen=True, slots=True)
class ResultadoProcesamiento:
    """Cuántos eventos de la cola se aplicaron y cuántos se apartaron por error."""

    procesados: int
    fallidos: int


@dataclass(frozen=True, slots=True)
class ResultadoIngesta:
    """Respuesta de sincronización hacia la sucursal."""

    ultimo_consecutivo_confirmado: int
    hueco_detectado_desde: int | None


def recibir_lote_eventos(
    conn: psycopg.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    eventos: list[EventoContrato],
) -> ResultadoIngesta:
    """Guarda eventos recibidos de forma idempotente y los encola en PostgreSQL.

    Antes de guardar nada valida cada evento contra el contrato y recalcula su
    hash. Si uno solo es inválido, se rechaza el lote completo.
    """
    for evento in eventos:
        validar_evento_recibido(evento)

    with conn.transaction():
        conn.execute(
            """
            INSERT INTO sincronizacion.estado_cajas (sucursal_id, caja_id)
            VALUES (%s, %s)
            ON CONFLICT (sucursal_id, caja_id) DO NOTHING
            """,
            (sucursal_id, caja_id),
        )
        fila_estado = conn.execute(
            """
            SELECT ultimo_consecutivo_confirmado
            FROM sincronizacion.estado_cajas
            WHERE sucursal_id = %s AND caja_id = %s
            FOR UPDATE
            """,
            (sucursal_id, caja_id),
        ).fetchone()
        assert fila_estado is not None
        confirmado = int(fila_estado["ultimo_consecutivo_confirmado"])

        for evento in sorted(eventos, key=lambda e: e.consecutivo):
            if evento.sucursal_id != sucursal_id or evento.caja_id != caja_id:
                raise ValueError("La identidad del evento no coincide con el lote.")

            existente = conn.execute(
                """
                SELECT hash FROM sincronizacion.eventos_recibidos
                WHERE sucursal_id = %s AND caja_id = %s AND consecutivo = %s
                """,
                (sucursal_id, caja_id, evento.consecutivo),
            ).fetchone()

            if existente is not None:
                if existente["hash"] != evento.hash:
                    raise ConflictoHashEventoError(
                        f"Conflicto de hash en {sucursal_id}/{caja_id}#{evento.consecutivo}."
                    )
                continue

            conn.execute(
                """
                INSERT INTO sincronizacion.eventos_recibidos (
                    sucursal_id, caja_id, consecutivo, tipo, version_esquema,
                    contenido, usuario_id, registrado_en, hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    evento.sucursal_id,
                    evento.caja_id,
                    evento.consecutivo,
                    evento.tipo.value,
                    evento.version_esquema,
                    Jsonb(evento.contenido),
                    evento.usuario_id,
                    evento.registrado_en,
                    evento.hash,
                ),
            )
            conn.execute(
                """
                INSERT INTO sincronizacion.cola_eventos (
                    sucursal_id, caja_id, consecutivo, tipo,
                    contenido, usuario_id, registrado_en
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sucursal_id, caja_id, consecutivo) DO NOTHING
                """,
                (
                    evento.sucursal_id,
                    evento.caja_id,
                    evento.consecutivo,
                    evento.tipo.value,
                    Jsonb(evento.contenido),
                    evento.usuario_id,
                    evento.registrado_en,
                ),
            )

        # Avanza la marca de agua solo mientras no existan huecos en los consecutivos.
        while True:
            siguiente = conn.execute(
                """
                SELECT 1 FROM sincronizacion.eventos_recibidos
                WHERE sucursal_id = %s AND caja_id = %s AND consecutivo = %s
                """,
                (sucursal_id, caja_id, confirmado + 1),
            ).fetchone()
            if siguiente is None:
                break
            confirmado += 1

        conn.execute(
            """
            UPDATE sincronizacion.estado_cajas
            SET ultimo_consecutivo_confirmado = %s, actualizado_en = %s
            WHERE sucursal_id = %s AND caja_id = %s
            """,
            (confirmado, datetime.now(UTC), sucursal_id, caja_id),
        )

        mayor_recibido = conn.execute(
            """
            SELECT COALESCE(MAX(consecutivo), 0) AS maximo
            FROM sincronizacion.eventos_recibidos
            WHERE sucursal_id = %s AND caja_id = %s
            """,
            (sucursal_id, caja_id),
        ).fetchone()
        maximo = int(mayor_recibido["maximo"]) if mayor_recibido else 0
        hueco = (confirmado + 1) if maximo > confirmado else None

        return ResultadoIngesta(
            ultimo_consecutivo_confirmado=confirmado,
            hueco_detectado_desde=hueco,
        )


def validar_evento_recibido(evento: EventoContrato) -> None:
    """Comprueba que el evento cumple el contrato y que su hash corresponde a sus datos."""
    if evento.version_esquema != VERSION_ESQUEMA_ACTUAL:
        raise EventoInvalidoError(
            f"Versión de esquema {evento.version_esquema} no soportada en "
            f"{evento.sucursal_id}/{evento.caja_id}#{evento.consecutivo}."
        )
    try:
        validar_contenido_por_tipo(evento.tipo, evento.contenido)
    except ValueError as error:
        raise EventoInvalidoError(
            f"Contenido inválido para '{evento.tipo}' en "
            f"{evento.sucursal_id}/{evento.caja_id}#{evento.consecutivo}: {error}"
        ) from error
    if evento.hash_esperado() != evento.hash:
        raise EventoInvalidoError(
            f"El hash no corresponde a los datos en "
            f"{evento.sucursal_id}/{evento.caja_id}#{evento.consecutivo}."
        )


def procesar_eventos_pendientes_cola(
    conn: psycopg.Connection, *, limite: int = 50
) -> ResultadoProcesamiento:
    """Toma eventos sin procesar usando FOR UPDATE SKIP LOCKED y los aplica.

    Cada evento se aplica en su propio savepoint: si uno falla, se deshacen
    solo sus cambios, se marca como fallido con el error y la cola sigue con
    los demás. Los fallidos se revisan con ``eventos_fallidos()``.

    Solo un proceso aplica la cola a la vez. Si el trabajador automático y
    ``/sincronizacion/procesar-cola`` coincidieran, cada uno actualizaría las
    mismas existencias en distinto orden y PostgreSQL los bloquearía entre sí.
    El segundo espera a que el primero termine su lote.

    Un error pasajero de PostgreSQL (bloqueo mutuo, conflicto de
    serialización) no es culpa del evento: se deshace el lote completo y se
    propaga, para que el trabajador lo repita más tarde.
    """
    procesados = 0
    fallidos = 0
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (CANDADO_COLA,))
        filas = conn.execute(
            """
            SELECT id, sucursal_id, caja_id, consecutivo, tipo, contenido, usuario_id
            FROM sincronizacion.cola_eventos
            WHERE procesado_en IS NULL AND fallido_en IS NULL
            ORDER BY id
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (limite,),
        ).fetchall()

        for fila in filas:
            try:
                with conn.transaction():  # savepoint por evento
                    _aplicar_evento_en_central(conn, fila)
                    conn.execute(
                        "UPDATE sincronizacion.cola_eventos SET procesado_en = %s WHERE id = %s",
                        (datetime.now(UTC), fila["id"]),
                    )
                procesados += 1
            except ERRORES_PASAJEROS:
                raise
            except Exception as error:  # noqa: BLE001 - se registra y la cola continúa
                conn.execute(
                    """
                    UPDATE sincronizacion.cola_eventos
                    SET fallido_en = %s, error = %s
                    WHERE id = %s
                    """,
                    (
                        datetime.now(UTC),
                        f"{type(error).__name__}: {error}"[:LONGITUD_MAXIMA_ERROR],
                        fila["id"],
                    ),
                )
                fallidos += 1

    return ResultadoProcesamiento(procesados=procesados, fallidos=fallidos)


def eventos_fallidos(conn: psycopg.Connection, *, limite: int = 100) -> list[dict[str, Any]]:
    """Eventos apartados por error al procesarse, del más antiguo al más reciente."""
    return conn.execute(
        """
        SELECT sucursal_id, caja_id, consecutivo, tipo, fallido_en, error
        FROM sincronizacion.cola_eventos
        WHERE fallido_en IS NOT NULL
        ORDER BY id
        LIMIT %s
        """,
        (limite,),
    ).fetchall()


def _aplicar_evento_en_central(conn: psycopg.Connection, fila: dict[str, Any]) -> None:
    sucursal_id: str = fila["sucursal_id"]
    caja_id: str = fila["caja_id"]
    consecutivo: int = fila["consecutivo"]
    usuario_id: str = fila["usuario_id"]
    tipo = TipoEvento(fila["tipo"])
    contenido: dict[str, Any] = fila["contenido"]

    # Si el usuario estaba revocado en central, se envía a auditoría (sección 6.4).
    usuario = conn.execute(
        "SELECT activo FROM usuarios.usuarios WHERE usuario_id = %s",
        (usuario_id,),
    ).fetchone()
    if usuario is not None and not usuario["activo"]:
        conn.execute(
            """
            INSERT INTO inventario.cola_auditoria (
                sucursal_id, caja_id, consecutivo, usuario_id, motivo
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (sucursal_id, caja_id, consecutivo) DO NOTHING
            """,
            (
                sucursal_id,
                caja_id,
                consecutivo,
                usuario_id,
                "Operación realizada por usuario revocado en central.",
            ),
        )

    if tipo is TipoEvento.VENTA:
        venta = ContenidoVenta.model_validate(contenido)
        for partida in venta.partidas:
            _actualizar_existencia_central(conn, sucursal_id, partida.sku, -partida.cantidad)
    elif tipo is TipoEvento.CANCELACION:
        cancelacion = ContenidoCancelacion.model_validate(contenido)
        original = conn.execute(
            """
            SELECT contenido FROM sincronizacion.eventos_recibidos
            WHERE sucursal_id = %s AND caja_id = %s AND consecutivo = %s
            """,
            (sucursal_id, caja_id, cancelacion.consecutivo_original),
        ).fetchone()
        if original is not None:
            venta_orig = ContenidoVenta.model_validate(original["contenido"])
            for partida in venta_orig.partidas:
                _actualizar_existencia_central(conn, sucursal_id, partida.sku, partida.cantidad)
    elif tipo is TipoEvento.RECEPCION_PROVEEDOR:
        recepcion = ContenidoRecepcionProveedor.model_validate(contenido)
        for partida in recepcion.partidas:
            _actualizar_existencia_central(conn, sucursal_id, partida.sku, partida.cantidad)
    elif tipo is TipoEvento.MERMA:
        merma = ContenidoMerma.model_validate(contenido)
        _actualizar_existencia_central(conn, sucursal_id, merma.sku, -merma.cantidad)
    elif tipo is TipoEvento.AJUSTE:
        ajuste = ContenidoAjuste.model_validate(contenido)
        _actualizar_existencia_central(conn, sucursal_id, ajuste.sku, ajuste.diferencia_cantidad)
        # Un ajuste implica que alguien contó ese producto: cierra la sospecha de fantasma.
        resolver_por_ajuste(conn, sucursal_id=sucursal_id, sku=ajuste.sku, usuario_id=usuario_id)
    elif tipo is TipoEvento.TRASPASO:
        traspaso = ContenidoTraspaso.model_validate(contenido)
        signo = -1 if traspaso.direccion == "salida" else 1
        for partida in traspaso.partidas:
            _actualizar_existencia_central(conn, sucursal_id, partida.sku, signo * partida.cantidad)


def _actualizar_existencia_central(
    conn: psycopg.Connection, sucursal_id: str, sku: str, delta: int
) -> None:
    conn.execute(
        """
        INSERT INTO inventario.existencias_sucursal (sucursal_id, sku, cantidad, actualizado_en)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sucursal_id, sku) DO UPDATE SET
            cantidad = inventario.existencias_sucursal.cantidad + excluded.cantidad,
            actualizado_en = excluded.actualizado_en
        """,
        (sucursal_id, sku, delta, datetime.now(UTC)),
    )

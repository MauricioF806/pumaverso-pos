"""Registro de eventos de la sucursal.

Todo lo que ocurre en la sucursal (una venta, una recepción de mercancía, un
ajuste) se guarda como un evento. El registro cumple tres reglas del PRD:

- **Append-only:** un evento nunca se modifica ni se borra. La base de datos lo
  impide con triggers (ver ``persistencia/migraciones.py``).
- **Consecutivo sin huecos:** el número se asigna dentro de la misma transacción
  que la operación. Si la operación falla, el número no se gasta.
- **Identidad sin reloj:** un evento se identifica por sucursal + caja +
  consecutivo. La fecha solo sirve para reportes.
"""

import json
import math
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pumaverso_contratos import VERSION_ESQUEMA_ACTUAL, TipoEvento, calcular_hash_evento


class FueraDeTransaccionError(RuntimeError):
    """Se intentó registrar un evento sin una transacción abierta."""


class ContenidoInvalidoError(ValueError):
    """El contenido del evento no se puede guardar como JSON."""


@dataclass(frozen=True, slots=True)
class Evento:
    """Un evento tal como quedó guardado en el registro."""

    sucursal_id: str
    caja_id: str
    consecutivo: int
    tipo: TipoEvento
    version_esquema: int
    contenido: dict[str, Any]
    usuario_id: str
    registrado_en: str
    hash: str

    @property
    def identidad(self) -> tuple[str, str, int]:
        """Identidad única del evento: (sucursal, caja, consecutivo)."""
        return (self.sucursal_id, self.caja_id, self.consecutivo)


def registrar_evento(
    conexion: sqlite3.Connection,
    *,
    sucursal_id: str,
    caja_id: str,
    tipo: TipoEvento | str,
    contenido: Mapping[str, Any],
    usuario_id: str,
    ahora: datetime | None = None,
) -> Evento:
    """Agrega un evento al registro y lo devuelve.

    Debe llamarse dentro de ``transaccion()``, junto con las demás escrituras de
    la misma operación, para que todo se guarde o nada se guarde.

    Para importes de dinero, usar enteros en centavos dentro de ``contenido``.
    """
    if not conexion.in_transaction:
        raise FueraDeTransaccionError(
            "registrar_evento() debe llamarse dentro de transaccion(), para que el "
            "evento y el resto de la operación se guarden juntos."
        )

    _validar_texto("sucursal_id", sucursal_id)
    _validar_texto("caja_id", caja_id)
    _validar_texto("usuario_id", usuario_id)
    tipo = TipoEvento(tipo)
    contenido_json = serializar_contenido(contenido)
    registrado_en = _marca_de_tiempo(ahora)

    consecutivo = _siguiente_consecutivo(conexion, sucursal_id, caja_id)
    huella = calcular_hash(
        sucursal_id=sucursal_id,
        caja_id=caja_id,
        consecutivo=consecutivo,
        tipo=tipo,
        version_esquema=VERSION_ESQUEMA_ACTUAL,
        contenido_json=contenido_json,
        usuario_id=usuario_id,
        registrado_en=registrado_en,
    )

    conexion.execute(
        """
        INSERT INTO eventos (
            sucursal_id, caja_id, consecutivo, tipo, version_esquema,
            contenido, usuario_id, registrado_en, hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sucursal_id,
            caja_id,
            consecutivo,
            tipo.value,
            VERSION_ESQUEMA_ACTUAL,
            contenido_json,
            usuario_id,
            registrado_en,
            huella,
        ),
    )

    return Evento(
        sucursal_id=sucursal_id,
        caja_id=caja_id,
        consecutivo=consecutivo,
        tipo=tipo,
        version_esquema=VERSION_ESQUEMA_ACTUAL,
        contenido=json.loads(contenido_json),
        usuario_id=usuario_id,
        registrado_en=registrado_en,
        hash=huella,
    )


def obtener_evento(
    conexion: sqlite3.Connection, sucursal_id: str, caja_id: str, consecutivo: int
) -> Evento | None:
    """Busca un evento por su identidad. Devuelve ``None`` si no existe."""
    fila = conexion.execute(
        "SELECT * FROM eventos WHERE sucursal_id = ? AND caja_id = ? AND consecutivo = ?",
        (sucursal_id, caja_id, consecutivo),
    ).fetchone()
    return _fila_a_evento(fila) if fila else None


def listar_eventos(
    conexion: sqlite3.Connection,
    sucursal_id: str,
    caja_id: str,
    *,
    desde_consecutivo: int = 1,
    limite: int = 100,
) -> list[Evento]:
    """Lista eventos en orden de consecutivo, a partir de ``desde_consecutivo``."""
    if limite < 1:
        raise ValueError("limite debe ser mayor que cero")
    filas = conexion.execute(
        """
        SELECT * FROM eventos
        WHERE sucursal_id = ? AND caja_id = ? AND consecutivo >= ?
        ORDER BY consecutivo
        LIMIT ?
        """,
        (sucursal_id, caja_id, desde_consecutivo, limite),
    ).fetchall()
    return [_fila_a_evento(fila) for fila in filas]


def ultimo_consecutivo(conexion: sqlite3.Connection, sucursal_id: str, caja_id: str) -> int:
    """Devuelve el último consecutivo registrado, o 0 si no hay eventos."""
    return conexion.execute(
        "SELECT COALESCE(MAX(consecutivo), 0) FROM eventos WHERE sucursal_id = ? AND caja_id = ?",
        (sucursal_id, caja_id),
    ).fetchone()[0]


def serializar_contenido(contenido: Mapping[str, Any]) -> str:
    """Convierte el contenido a JSON canónico: mismo contenido, mismo texto.

    Las llaves se ordenan y no hay espacios, para que el hash no dependa del
    orden en que se escribieron los datos.
    """
    if not isinstance(contenido, Mapping):
        raise ContenidoInvalidoError("El contenido de un evento debe ser un diccionario.")
    _rechazar_flotantes_no_finitos(contenido)
    try:
        return json.dumps(
            contenido,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ContenidoInvalidoError(
            f"El contenido del evento no se puede convertir a JSON: {error}"
        ) from error


def calcular_hash(
    *,
    sucursal_id: str,
    caja_id: str,
    consecutivo: int,
    tipo: TipoEvento | str,
    version_esquema: int,
    contenido_json: str,
    usuario_id: str,
    registrado_en: str,
) -> str:
    """Calcula el hash SHA-256 de un evento.

    Usa la función del contrato compartido para que central obtenga el mismo
    resultado al verificarlo. Central lo usa también para distinguir un
    reintento (mismo hash) de un conflicto (hash distinto).
    """
    return calcular_hash_evento(
        sucursal_id=sucursal_id,
        caja_id=caja_id,
        consecutivo=consecutivo,
        tipo=tipo,
        version_esquema=version_esquema,
        contenido=json.loads(contenido_json),
        usuario_id=usuario_id,
        registrado_en=registrado_en,
    )


def verificar_hash(evento: Evento) -> bool:
    """Indica si el hash guardado corresponde a los datos del evento."""
    esperado = calcular_hash(
        sucursal_id=evento.sucursal_id,
        caja_id=evento.caja_id,
        consecutivo=evento.consecutivo,
        tipo=evento.tipo,
        version_esquema=evento.version_esquema,
        contenido_json=serializar_contenido(evento.contenido),
        usuario_id=evento.usuario_id,
        registrado_en=evento.registrado_en,
    )
    return esperado == evento.hash


def _siguiente_consecutivo(conexion: sqlite3.Connection, sucursal_id: str, caja_id: str) -> int:
    return ultimo_consecutivo(conexion, sucursal_id, caja_id) + 1


def _marca_de_tiempo(ahora: datetime | None) -> str:
    momento = ahora if ahora is not None else datetime.now(UTC)
    if momento.tzinfo is None:
        raise ValueError("La fecha del evento debe incluir zona horaria.")
    return momento.astimezone(UTC).isoformat(timespec="milliseconds")


def _validar_texto(nombre: str, valor: str) -> None:
    if not isinstance(valor, str) or not valor.strip():
        raise ValueError(f"{nombre} no puede estar vacío.")


def _rechazar_flotantes_no_finitos(valor: Any) -> None:
    if isinstance(valor, float) and not math.isfinite(valor):
        raise ContenidoInvalidoError("El contenido no puede tener NaN ni infinito.")
    if isinstance(valor, Mapping):
        for llave, interno in valor.items():
            if not isinstance(llave, str):
                raise ContenidoInvalidoError("Las llaves del contenido deben ser texto.")
            _rechazar_flotantes_no_finitos(interno)
    elif isinstance(valor, (list, tuple)):
        for interno in valor:
            _rechazar_flotantes_no_finitos(interno)


def _fila_a_evento(fila: sqlite3.Row) -> Evento:
    return Evento(
        sucursal_id=fila["sucursal_id"],
        caja_id=fila["caja_id"],
        consecutivo=fila["consecutivo"],
        tipo=TipoEvento(fila["tipo"]),
        version_esquema=fila["version_esquema"],
        contenido=json.loads(fila["contenido"]),
        usuario_id=fila["usuario_id"],
        registrado_en=fila["registrado_en"],
        hash=fila["hash"],
    )

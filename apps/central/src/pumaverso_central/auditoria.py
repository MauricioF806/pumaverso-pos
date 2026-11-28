"""Cola de auditoría (PRD: usuario dado de baja operando sin conexión; decisión 0012).

Una tienda sin conexión no se entera de que un usuario fue dado de baja hasta
24 h después (vigencia de credenciales). Lo que ese usuario haga en ese tiempo
se acepta, porque la venta ya ocurrió, pero al llegar a central se aparta aquí
para que alguien lo revise. Esta consulta y la marca de revisado cierran ese
ciclo.
"""

from datetime import UTC, datetime
from typing import Any

import psycopg


class RegistroNoEncontradoError(LookupError):
    """No existe el registro o ya estaba revisado."""


def listar_auditoria(
    conn: psycopg.Connection,
    *,
    sucursal_id: str | None = None,
    incluir_revisadas: bool = False,
    limite: int = 100,
) -> list[dict[str, Any]]:
    """Registros de la cola, con qué hizo el usuario y cuándo, más recientes primero."""
    condiciones = []
    parametros: list[Any] = []
    if sucursal_id is not None:
        condiciones.append("a.sucursal_id = %s")
        parametros.append(sucursal_id)
    if not incluir_revisadas:
        condiciones.append("a.revisada_en IS NULL")
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    parametros.append(limite)
    return conn.execute(
        f"""
        SELECT a.id, a.sucursal_id, a.caja_id, a.consecutivo, a.usuario_id, a.motivo,
               a.registrado_en AS apartado_en,
               e.tipo, e.registrado_en AS operacion_en,
               (e.contenido ->> 'total_centavos')::int AS total_centavos,
               a.revisada_en, a.revisada_por, a.nota
        FROM inventario.cola_auditoria a
        LEFT JOIN sincronizacion.eventos_recibidos e
          ON e.sucursal_id = a.sucursal_id AND e.caja_id = a.caja_id
         AND e.consecutivo = a.consecutivo
        {donde}
        ORDER BY a.registrado_en DESC, a.id DESC
        LIMIT %s
        """,
        parametros,
    ).fetchall()


def marcar_revisada(
    conn: psycopg.Connection, *, registro_id: int, revisada_por: str, nota: str
) -> dict[str, Any]:
    """Deja constancia de quién revisó el registro y qué concluyó."""
    with conn.transaction():
        fila = conn.execute(
            """
            UPDATE inventario.cola_auditoria
            SET revisada_en = %s, revisada_por = %s, nota = %s
            WHERE id = %s AND revisada_en IS NULL
            RETURNING id, revisada_en, revisada_por, nota
            """,
            (datetime.now(UTC), revisada_por, nota, registro_id),
        ).fetchone()
    if fila is None:
        raise RegistroNoEncontradoError(
            f"No hay un registro pendiente de revisar con id {registro_id}."
        )
    return fila

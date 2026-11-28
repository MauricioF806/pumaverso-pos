"""Detección de inventario fantasma (PRD 5.3, decisión 0009).

Inventario fantasma: el sistema dice que hay piezas, pero no están en el
anaquel, así que el producto deja de venderse. Se sospecha cuando las ventas
de un SKU en una franja horaria caen muy por debajo de su ritmo normal en esa
sucursal y franja.

Cómo se decide, por cada sucursal, SKU y franja:

1. **Ritmo esperado (λ):** promedio de piezas vendidas en la misma franja de
   los últimos ``dias_historia`` días en que la sucursal operó.
2. **¿Es raro vender tan poco?** Con un ritmo λ, las ventas de una franja se
   comportan como una distribución de Poisson. Si la probabilidad de vender
   esa cantidad o menos por simple azar es menor que ``significancia``, es
   sospecha. Así el umbral sale del ritmo de cada SKU en cada sucursal y no de
   un número fijo de días sin venta.
3. **Solo con datos completos:** una franja se evalúa cuando central ya tiene
   operaciones de la sucursal posteriores a su fin. Si la sucursal estuvo sin
   conexión, la franja se evalúa cuando vuelva, no antes; si no, la falta de
   ventas que aún no llegan parecería fantasma.

Una sospecha queda ``pendiente_conteo`` (el gerente debe contar), pasa a
``escalada`` si nadie cuenta en ``horas_para_escalar`` y se ``resuelve`` con un
conteo físico o con un ajuste de inventario de ese SKU enviado por la tienda.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

ESTADO_PENDIENTE = "pendiente_conteo"
ESTADO_ESCALADA = "escalada"
ESTADO_RESUELTA = "resuelta"
ESTADOS_ABIERTOS = (ESTADO_PENDIENTE, ESTADO_ESCALADA)


@dataclass(frozen=True, slots=True)
class ParametrosFantasma:
    """Ajustes de la detección; los valores por defecto vienen de la configuración."""

    zona_horaria: str = "America/Mexico_City"
    horas_por_franja: int = 4
    dias_historia: int = 28
    dias_minimos: int = 7
    significancia: float = 0.01
    horas_para_escalar: float = 24.0

    def __post_init__(self) -> None:
        if 24 % self.horas_por_franja != 0:
            raise ValueError("horas_por_franja debe dividir 24 (1, 2, 3, 4, 6, 8, 12 o 24).")


@dataclass(frozen=True, slots=True)
class Franja:
    """Bloque de horas de un día en la hora local de la sucursal."""

    inicio: datetime  # con zona horaria local
    fin: datetime

    def del_dia_anterior(self, dias: int) -> "Franja":
        # Aritmética de reloj de pared: la misma hora local de días anteriores.
        return Franja(self.inicio - timedelta(days=dias), self.fin - timedelta(days=dias))


@dataclass(slots=True)
class ResultadoDeteccion:
    """Qué hizo una corrida de la detección."""

    franjas_evaluadas: int = 0
    franjas_pendientes: list[dict[str, Any]] = field(default_factory=list)
    nuevas: list[dict[str, Any]] = field(default_factory=list)
    escaladas: int = 0


class SospechaNoEncontradaError(LookupError):
    """No existe la sospecha o ya estaba resuelta."""


def probabilidad_de_vender_tan_poco(vendidas: int, esperadas: float) -> float:
    """P(X ≤ vendidas) para X ~ Poisson(esperadas)."""
    if esperadas <= 0:
        return 1.0
    total = 0.0
    for k in range(vendidas + 1):
        total += math.exp(-esperadas + k * math.log(esperadas) - math.lgamma(k + 1))
    return min(total, 1.0)


def umbral_de_sospecha(esperadas: float, significancia: float) -> int | None:
    """Máximo de piezas vendidas que todavía se considera sospechoso.

    ``None`` si ni vender cero es lo bastante raro: el SKU se vende tan poco en
    esa franja que no hay forma de distinguir un anaquel vacío del azar.
    """
    umbral: int | None = None
    k = 0
    while probabilidad_de_vender_tan_poco(k, esperadas) < significancia:
        umbral = k
        k += 1
    return umbral


def franjas_a_revisar(ahora: datetime, parametros: ParametrosFantasma) -> list[Franja]:
    """Franjas ya terminadas de las últimas 24 horas, de la más antigua a la más reciente."""
    zona = ZoneInfo(parametros.zona_horaria)
    local = ahora.astimezone(zona)
    horas = parametros.horas_por_franja
    inicio_actual = local.replace(
        hour=(local.hour // horas) * horas, minute=0, second=0, microsecond=0
    )
    cantidad = 24 // horas
    franjas = []
    for atras in range(cantidad, 0, -1):
        inicio = inicio_actual - timedelta(hours=horas * atras)
        franjas.append(Franja(inicio, inicio + timedelta(hours=horas)))
    return franjas


def detectar_inventario_fantasma(
    conn: psycopg.Connection,
    *,
    parametros: ParametrosFantasma,
    ahora: datetime | None = None,
) -> ResultadoDeteccion:
    """Revisa las franjas terminadas de cada sucursal y registra sospechas nuevas.

    Se puede correr cuantas veces se quiera: cada franja se evalúa una sola vez
    por sucursal y nunca hay dos sospechas abiertas del mismo SKU.
    """
    momento = ahora or datetime.now(UTC)
    resultado = ResultadoDeteccion()
    with conn.transaction():
        resultado.escaladas = _escalar_sin_conteo(conn, momento, parametros)
        for sucursal_id in _sucursales(conn):
            ultimo_dato = _ultima_operacion_confirmada(conn, sucursal_id)
            for franja in franjas_a_revisar(momento, parametros):
                if _ya_evaluada(conn, sucursal_id, franja):
                    continue
                if ultimo_dato is None or ultimo_dato < franja.fin:
                    resultado.franjas_pendientes.append(
                        {
                            "sucursal_id": sucursal_id,
                            "franja_inicio": franja.inicio.isoformat(),
                            "motivo": "Central aún no recibe las operaciones de esa franja.",
                        }
                    )
                    continue
                nuevas = _evaluar_franja(conn, sucursal_id, franja, momento, parametros)
                resultado.franjas_evaluadas += 1
                resultado.nuevas.extend(nuevas)
    return resultado


def listar_sospechas(
    conn: psycopg.Connection,
    *,
    sucursal_id: str | None = None,
    solo_abiertas: bool = True,
) -> list[dict[str, Any]]:
    """Reporte para el gerente: sospechas con el nombre del producto, más recientes primero."""
    condiciones = []
    parametros: list[Any] = []
    if sucursal_id is not None:
        condiciones.append("s.sucursal_id = %s")
        parametros.append(sucursal_id)
    if solo_abiertas:
        condiciones.append("s.estado <> %s")
        parametros.append(ESTADO_RESUELTA)
    donde = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    return conn.execute(
        f"""
        SELECT s.id, s.sucursal_id, s.sku, p.nombre, s.estado,
               s.franja_inicio, s.franja_fin, s.unidades_esperadas, s.unidades_vendidas,
               s.umbral_unidades, s.probabilidad, s.existencia_registrada,
               s.venta_perdida_estimada_centavos, s.franjas_afectadas,
               s.detectada_en, s.escalada_en, s.resuelta_en, s.resolucion,
               s.cantidad_contada, s.resuelta_por
        FROM inventario.sospechas_fantasma s
        LEFT JOIN catalogo.productos p ON p.sku = s.sku
        {donde}
        ORDER BY s.detectada_en DESC, s.id DESC
        """,
        parametros,
    ).fetchall()


def registrar_conteo(
    conn: psycopg.Connection,
    *,
    sospecha_id: int,
    cantidad_contada: int,
    usuario_id: str,
    sucursal_id: str | None = None,
    ahora: datetime | None = None,
) -> dict[str, Any]:
    """Cierra una sospecha con el conteo físico del gerente.

    Con ``sucursal_id``, solo cierra sospechas de esa sucursal: una sucursal no
    puede cerrar las de otra.

    Devuelve la diferencia contra lo que central tiene registrado. Si no es
    cero, el gerente registra el ajuste en la tienda, que es la dueña del
    inventario físico (PRD 5.1).
    """
    momento = ahora or datetime.now(UTC)
    with conn.transaction():
        fila = conn.execute(
            """
            SELECT sucursal_id, sku FROM inventario.sospechas_fantasma
            WHERE id = %s AND estado <> %s
            FOR UPDATE
            """,
            (sospecha_id, ESTADO_RESUELTA),
        ).fetchone()
        if fila is None or (sucursal_id is not None and fila["sucursal_id"] != sucursal_id):
            raise SospechaNoEncontradaError(f"No hay una sospecha abierta con id {sospecha_id}.")
        registrada = _existencia(conn, fila["sucursal_id"], fila["sku"])
        conn.execute(
            """
            UPDATE inventario.sospechas_fantasma
            SET estado = %s, resuelta_en = %s, resolucion = 'conteo',
                cantidad_contada = %s, resuelta_por = %s
            WHERE id = %s
            """,
            (ESTADO_RESUELTA, momento, cantidad_contada, usuario_id, sospecha_id),
        )
    return {
        "sospecha_id": sospecha_id,
        "sucursal_id": fila["sucursal_id"],
        "sku": fila["sku"],
        "existencia_registrada": registrada,
        "cantidad_contada": cantidad_contada,
        "diferencia": cantidad_contada - registrada,
    }


def resolver_por_ajuste(
    conn: psycopg.Connection, *, sucursal_id: str, sku: str, usuario_id: str
) -> None:
    """Un ajuste de inventario de la tienda cierra la sospecha abierta de ese SKU.

    Se llama dentro de la transacción del procesamiento de la cola.
    """
    conn.execute(
        """
        UPDATE inventario.sospechas_fantasma
        SET estado = %s, resuelta_en = %s, resolucion = 'ajuste', resuelta_por = %s
        WHERE sucursal_id = %s AND sku = %s AND estado <> %s
        """,
        (ESTADO_RESUELTA, datetime.now(UTC), usuario_id, sucursal_id, sku, ESTADO_RESUELTA),
    )


# --- Internos -----------------------------------------------------------------


def _sucursales(conn: psycopg.Connection) -> list[str]:
    filas = conn.execute(
        "SELECT DISTINCT sucursal_id FROM sincronizacion.estado_cajas ORDER BY sucursal_id"
    ).fetchall()
    return [f["sucursal_id"] for f in filas]


def _ultima_operacion_confirmada(conn: psycopg.Connection, sucursal_id: str) -> datetime | None:
    """Fecha de la operación más reciente que central tiene sin huecos antes de ella."""
    fila = conn.execute(
        """
        SELECT MAX(e.registrado_en) AS ultima
        FROM sincronizacion.eventos_recibidos e
        JOIN sincronizacion.estado_cajas c
          ON c.sucursal_id = e.sucursal_id AND c.caja_id = e.caja_id
        WHERE e.sucursal_id = %s AND e.consecutivo <= c.ultimo_consecutivo_confirmado
        """,
        (sucursal_id,),
    ).fetchone()
    return fila["ultima"] if fila else None


def _ya_evaluada(conn: psycopg.Connection, sucursal_id: str, franja: Franja) -> bool:
    return (
        conn.execute(
            """
            SELECT 1 FROM inventario.franjas_evaluadas
            WHERE sucursal_id = %s AND franja_inicio = %s
            """,
            (sucursal_id, franja.inicio),
        ).fetchone()
        is not None
    )


def _ventas_por_franja(
    conn: psycopg.Connection,
    sucursal_id: str,
    franja: Franja,
    dias_historia: int,
) -> dict[int, dict[str, int]]:
    """Piezas vendidas por SKU en la franja de hoy (0) y en la misma franja de días atrás.

    Las ventas canceladas no cuentan. Solo aparecen los días con al menos una
    venta en la franja: si no hubo ninguna, la sucursal no operó a esa hora.
    """
    desde = franja.del_dia_anterior(dias_historia).inicio
    filas = conn.execute(
        """
        SELECT v.registrado_en, p ->> 'sku' AS sku, (p ->> 'cantidad')::int AS cantidad
        FROM sincronizacion.eventos_recibidos v
        CROSS JOIN LATERAL jsonb_array_elements(v.contenido -> 'partidas') AS p
        WHERE v.sucursal_id = %s AND v.tipo = 'venta'
          AND v.registrado_en >= %s AND v.registrado_en < %s
          AND NOT EXISTS (
              SELECT 1 FROM sincronizacion.eventos_recibidos c
              WHERE c.sucursal_id = v.sucursal_id AND c.caja_id = v.caja_id
                AND c.tipo = 'cancelacion'
                AND (c.contenido ->> 'consecutivo_original')::int = v.consecutivo
          )
        """,
        (sucursal_id, desde, franja.fin),
    ).fetchall()

    zona = franja.inicio.tzinfo
    hora_inicio = franja.inicio.hour
    hora_fin = hora_inicio + int((franja.fin - franja.inicio).total_seconds() // 3600)
    dia_evaluado: date = franja.inicio.date()
    por_dia: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for fila in filas:
        local = fila["registrado_en"].astimezone(zona)
        if not hora_inicio <= local.hour < hora_fin:
            continue
        dias_atras = (dia_evaluado - local.date()).days
        if 0 <= dias_atras <= dias_historia:
            por_dia[dias_atras][fila["sku"]] += fila["cantidad"]
    return por_dia


def _evaluar_franja(
    conn: psycopg.Connection,
    sucursal_id: str,
    franja: Franja,
    momento: datetime,
    parametros: ParametrosFantasma,
) -> list[dict[str, Any]]:
    por_dia = _ventas_por_franja(conn, sucursal_id, franja, parametros.dias_historia)
    hoy = por_dia.get(0)
    dias_previos = [d for d in por_dia if d > 0]

    if hoy is None:
        _marcar_evaluada(conn, sucursal_id, franja, "sin_operacion", momento)
        return []
    if len(dias_previos) < parametros.dias_minimos:
        _marcar_evaluada(conn, sucursal_id, franja, "historia_insuficiente", momento)
        return []

    totales: dict[str, int] = defaultdict(int)
    for dia in dias_previos:
        for sku, cantidad in por_dia[dia].items():
            totales[sku] += cantidad

    abiertas = {
        f["sku"]: f["id"]
        for f in conn.execute(
            """
            SELECT id, sku FROM inventario.sospechas_fantasma
            WHERE sucursal_id = %s AND estado <> %s
            """,
            (sucursal_id, ESTADO_RESUELTA),
        ).fetchall()
    }
    precios = {
        f["sku"]: f["precio_centavos"]
        for f in conn.execute(
            "SELECT sku, precio_centavos FROM catalogo.productos WHERE activo"
        ).fetchall()
    }

    nuevas = []
    for sku, total in sorted(totales.items()):
        esperadas = total / len(dias_previos)
        vendidas = hoy.get(sku, 0)
        perdida = max(0.0, esperadas - vendidas) * precios.get(sku, 0)

        if sku in abiertas:
            # Mientras siga abierta, cada franja con faltante suma a la venta perdida.
            if perdida > 0:
                conn.execute(
                    """
                    UPDATE inventario.sospechas_fantasma
                    SET venta_perdida_estimada_centavos = venta_perdida_estimada_centavos + %s,
                        franjas_afectadas = franjas_afectadas + 1
                    WHERE id = %s
                    """,
                    (round(perdida), abiertas[sku]),
                )
            continue
        if sku not in precios:
            continue  # producto dado de baja: no se resurte

        umbral = umbral_de_sospecha(esperadas, parametros.significancia)
        if umbral is None or vendidas > umbral:
            continue
        existencia = _existencia(conn, sucursal_id, sku)
        if existencia <= 0:
            continue  # el sistema ya sabe que no hay: no es fantasma

        probabilidad = probabilidad_de_vender_tan_poco(vendidas, esperadas)
        fila = conn.execute(
            """
            INSERT INTO inventario.sospechas_fantasma (
                sucursal_id, sku, franja_inicio, franja_fin, unidades_esperadas,
                unidades_vendidas, umbral_unidades, probabilidad, dias_historia,
                existencia_registrada, venta_perdida_estimada_centavos, estado, detectada_en
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                sucursal_id,
                sku,
                franja.inicio,
                franja.fin,
                round(esperadas, 2),
                vendidas,
                umbral,
                probabilidad,
                len(dias_previos),
                existencia,
                round(perdida),
                ESTADO_PENDIENTE,
                momento,
            ),
        ).fetchone()
        nuevas.append(
            {
                "id": fila["id"],
                "sucursal_id": sucursal_id,
                "sku": sku,
                "franja_inicio": franja.inicio.isoformat(),
                "unidades_esperadas": round(esperadas, 2),
                "unidades_vendidas": vendidas,
                "probabilidad": probabilidad,
                "existencia_registrada": existencia,
            }
        )

    _marcar_evaluada(conn, sucursal_id, franja, "evaluada", momento)
    return nuevas


def _marcar_evaluada(
    conn: psycopg.Connection, sucursal_id: str, franja: Franja, resultado: str, momento: datetime
) -> None:
    conn.execute(
        """
        INSERT INTO inventario.franjas_evaluadas (sucursal_id, franja_inicio, resultado, evaluada_en)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (sucursal_id, franja_inicio) DO NOTHING
        """,
        (sucursal_id, franja.inicio, resultado, momento),
    )


def _existencia(conn: psycopg.Connection, sucursal_id: str, sku: str) -> int:
    fila = conn.execute(
        """
        SELECT cantidad FROM inventario.existencias_sucursal
        WHERE sucursal_id = %s AND sku = %s
        """,
        (sucursal_id, sku),
    ).fetchone()
    return int(fila["cantidad"]) if fila else 0


def _escalar_sin_conteo(
    conn: psycopg.Connection, momento: datetime, parametros: ParametrosFantasma
) -> int:
    limite = momento - timedelta(hours=parametros.horas_para_escalar)
    cursor = conn.execute(
        """
        UPDATE inventario.sospechas_fantasma
        SET estado = %s, escalada_en = %s
        WHERE estado = %s AND detectada_en <= %s
        """,
        (ESTADO_ESCALADA, momento, ESTADO_PENDIENTE, limite),
    )
    return cursor.rowcount

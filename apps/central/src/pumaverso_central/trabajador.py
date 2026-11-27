"""Procesamiento automático de la cola de eventos en segundo plano (decisión 0007).

Cada ciclo procesa un lote de la cola:

- **Lote lleno:** quedan más eventos; sigue de inmediato.
- **Lote incompleto o vacío:** espera ``intervalo`` segundos.
- **Falla PostgreSQL:** reintenta con espera exponencial y margen aleatorio
  hasta ``espera_maxima``.

Los eventos que fallan al aplicarse no cuentan como falla del trabajador: la
cola los aparta (decisión 0006) y el ciclo sigue normal.
"""

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import psycopg

from pumaverso_central.cola import procesar_eventos_pendientes_cola

registro = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EstadoTrabajadorCola:
    """Lo que el trabajador sabe de sus últimos ciclos."""

    ultimo_ciclo_en: datetime | None = None
    procesados_total: int = 0
    fallidos_total: int = 0
    fallos_consecutivos: int = 0
    ultimo_error: str | None = None


def espera_con_jitter(
    intento: int, *, base: float, maximo: float, rng: random.Random | None = None
) -> float:
    """Espera exponencial con margen aleatorio completo (*full jitter*)."""
    techo = min(maximo, base * (2 ** (intento - 1)))
    return (rng or random.SystemRandom()).uniform(0.0, techo)


class TrabajadorCola:
    """Procesa la cola de eventos de central en ciclos."""

    def __init__(
        self,
        *,
        abrir_conexion: Callable[[], psycopg.Connection],
        lote: int,
        intervalo_segundos: float,
        espera_maxima_segundos: float = 60.0,
        rng: random.Random | None = None,
    ) -> None:
        self._abrir_conexion = abrir_conexion
        self._lote = lote
        self._intervalo = intervalo_segundos
        self._espera_maxima = espera_maxima_segundos
        self._rng = rng
        self._estado = EstadoTrabajadorCola()

    @property
    def estado(self) -> EstadoTrabajadorCola:
        return self._estado

    def ejecutar_ciclo(self) -> float:
        """Procesa un lote y devuelve cuántos segundos esperar antes del siguiente."""
        estado = replace(self._estado, ultimo_ciclo_en=datetime.now(UTC))
        try:
            with self._abrir_conexion() as conn:
                resultado = procesar_eventos_pendientes_cola(conn, limite=self._lote)
        except Exception as error:  # noqa: BLE001 - el trabajador nunca debe detenerse
            fallos = estado.fallos_consecutivos + 1
            self._estado = replace(
                estado,
                fallos_consecutivos=fallos,
                ultimo_error=f"{type(error).__name__}: {error}",
            )
            espera = espera_con_jitter(fallos, base=1.0, maximo=self._espera_maxima, rng=self._rng)
            registro.warning("No se pudo procesar la cola; reintento en %.1f s: %s", espera, error)
            return espera

        self._estado = replace(
            estado,
            procesados_total=estado.procesados_total + resultado.procesados,
            fallidos_total=estado.fallidos_total + resultado.fallidos,
            fallos_consecutivos=0,
            ultimo_error=None,
        )
        lote_lleno = resultado.procesados + resultado.fallidos >= self._lote
        return 0.0 if lote_lleno else self._intervalo

    async def ejecutar_siempre(self, detener: asyncio.Event) -> None:
        """Repite ciclos hasta que ``detener`` se active. Cada ciclo corre en un hilo."""
        while not detener.is_set():
            espera = await asyncio.to_thread(self.ejecutar_ciclo)
            try:
                await asyncio.wait_for(detener.wait(), timeout=espera)
            except TimeoutError:
                continue

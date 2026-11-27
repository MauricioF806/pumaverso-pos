"""Sincronización automática en segundo plano (decisión 0007).

Arranca con la app y repite ciclos de ``sincronizar_nodo()``:

- **Todo bien:** espera ``intervalo`` segundos para el siguiente ciclo.
- **Quedan eventos pendientes** (el lote se llenó): sigue casi de inmediato,
  para vaciar rápido lo acumulado durante una caída.
- **Central no responde:** reintenta con espera exponencial y margen aleatorio
  (1 s, 2 s, 4 s… hasta ``espera_maxima``) para que todas las sucursales no
  reintenten al mismo tiempo cuando central vuelva.
- **Conflicto (central rechaza los eventos):** reintentar no lo arregla. Sigue
  con el intervalo normal para seguir recibiendo bajas y precios, y deja el
  aviso en el estado para que alguien lo revise.

Ningún error detiene al trabajador: se registra y se vuelve a intentar.
"""

import asyncio
import logging
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from pumaverso_tienda.sincronizacion.servicio import (
    ConflictoSincronizacionError,
    ErrorSincronizacion,
    TransporteCentral,
    calcular_espera_reintento,
    sincronizar_nodo,
)

registro = logging.getLogger(__name__)

# Pausa entre ciclos mientras quedan eventos acumulados por enviar.
PAUSA_CON_PENDIENTES_SEGUNDOS = 1.0


@dataclass(frozen=True, slots=True)
class EstadoTrabajador:
    """Lo que el trabajador sabe de sus últimos ciclos."""

    ultimo_intento_en: datetime | None = None
    ultimo_exito_en: datetime | None = None
    fallos_consecutivos: int = 0
    en_conflicto: bool = False
    ultimo_error: str | None = None
    proximo_intento_en: datetime | None = None


class TrabajadorSincronizacion:
    """Ejecuta ciclos de sincronización y decide cuánto esperar entre uno y otro."""

    def __init__(
        self,
        *,
        abrir_conexion: Callable[[], sqlite3.Connection],
        crear_transporte: Callable[[], TransporteCentral],
        sucursal_id: str,
        caja_id: str,
        limite_lote: int,
        intervalo_segundos: float,
        espera_maxima_segundos: float,
        rng: random.Random | None = None,
    ) -> None:
        self._abrir_conexion = abrir_conexion
        self._crear_transporte = crear_transporte
        self._sucursal_id = sucursal_id
        self._caja_id = caja_id
        self._limite_lote = limite_lote
        self._intervalo = intervalo_segundos
        self._espera_maxima = espera_maxima_segundos
        self._rng = rng
        self._estado = EstadoTrabajador()

    @property
    def estado(self) -> EstadoTrabajador:
        return self._estado

    def ejecutar_ciclo(self) -> float:
        """Ejecuta un ciclo y devuelve cuántos segundos esperar antes del siguiente."""
        inicio = datetime.now(UTC)
        estado = replace(self._estado, ultimo_intento_en=inicio)
        try:
            conexion = self._abrir_conexion()
            try:
                resultado = sincronizar_nodo(
                    conexion,
                    sucursal_id=self._sucursal_id,
                    caja_id=self._caja_id,
                    transporte=self._crear_transporte(),
                    limite_lote=self._limite_lote,
                )
            finally:
                conexion.close()
        except ConflictoSincronizacionError as error:
            registro.warning("Sincronización en conflicto, requiere revisión: %s", error)
            estado = replace(
                estado, fallos_consecutivos=0, en_conflicto=True, ultimo_error=str(error)
            )
            espera = self._intervalo
        except ErrorSincronizacion as error:
            estado = self._registrar_fallo(estado, error)
            espera = self._espera_tras_fallo(estado.fallos_consecutivos)
            registro.info("Central no disponible; reintento en %.1f s: %s", espera, error)
        except Exception as error:  # noqa: BLE001 - el trabajador nunca debe detenerse
            registro.exception("Error inesperado al sincronizar")
            estado = self._registrar_fallo(estado, error)
            espera = self._espera_tras_fallo(estado.fallos_consecutivos)
        else:
            estado = replace(
                estado,
                ultimo_exito_en=inicio,
                fallos_consecutivos=0,
                en_conflicto=False,
                ultimo_error=None,
            )
            quedan_pendientes = resultado.eventos_enviados >= self._limite_lote
            espera = (
                min(PAUSA_CON_PENDIENTES_SEGUNDOS, self._intervalo)
                if quedan_pendientes
                else self._intervalo
            )

        self._estado = replace(
            estado, proximo_intento_en=datetime.now(UTC) + timedelta(seconds=espera)
        )
        return espera

    async def ejecutar_siempre(self, detener: asyncio.Event) -> None:
        """Repite ciclos hasta que ``detener`` se active. Cada ciclo corre en un hilo."""
        while not detener.is_set():
            espera = await asyncio.to_thread(self.ejecutar_ciclo)
            try:
                await asyncio.wait_for(detener.wait(), timeout=espera)
            except TimeoutError:
                continue

    def _registrar_fallo(self, estado: EstadoTrabajador, error: Exception) -> EstadoTrabajador:
        return replace(
            estado,
            fallos_consecutivos=estado.fallos_consecutivos + 1,
            ultimo_error=f"{type(error).__name__}: {error}",
        )

    def _espera_tras_fallo(self, fallos: int) -> float:
        return calcular_espera_reintento(
            fallos, base_segundos=1.0, max_segundos=self._espera_maxima, rng=self._rng
        )

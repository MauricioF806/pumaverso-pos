"""Límite de tasa por sucursal (PRD, protección contra avalancha; decisión 0012).

Si muchas sucursales vuelven a la vez después de una caída, todas quieren
sincronizar al mismo tiempo. La tienda ya se cuida sola (lotes acotados y
espera creciente con margen aleatorio); este límite es la defensa del lado de
central: una sucursal que pide demasiado recibe 429 y vuelve a intentar más
tarde, sin perder nada.

Cubeta de fichas: cada sucursal tiene ``rafaga`` fichas, cada petición gasta
una y se recuperan a razón de ``por_minuto``. Vive en la memoria del proceso;
con un solo proceso de central (V1) basta.
"""

import threading
import time
from collections.abc import Callable


class LimitadorPorSucursal:
    def __init__(self, reloj: Callable[[], float] = time.monotonic) -> None:
        self._reloj = reloj
        self._cubetas: dict[str, tuple[float, float]] = {}
        self._candado = threading.Lock()

    def intentar(self, sucursal_id: str, *, por_minuto: int, rafaga: int) -> float | None:
        """Gasta una ficha. Devuelve ``None`` si pasa, o cuántos segundos esperar."""
        tasa = por_minuto / 60
        with self._candado:
            ahora = self._reloj()
            fichas, ultimo = self._cubetas.get(sucursal_id, (float(rafaga), ahora))
            fichas = min(float(rafaga), fichas + (ahora - ultimo) * tasa)
            if fichas >= 1:
                self._cubetas[sucursal_id] = (fichas - 1, ahora)
                return None
            self._cubetas[sucursal_id] = (fichas, ahora)
            return (1 - fichas) / tasa

    def reiniciar(self) -> None:
        with self._candado:
            self._cubetas.clear()

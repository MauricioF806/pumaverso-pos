# 0007. Sincronización y cola automáticas dentro de cada aplicación

- **Estado:** Aceptada
- **Fecha:** 2026-11-27
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

Hasta ahora la tienda solo sincronizaba si alguien llamaba a `POST /sincronizacion/ejecutar`, y central solo procesaba su cola con `POST /sincronizacion/procesar-cola`. El PRD pide que la sucursal se recupere sola al volver la conexión, sin intervención de nadie.

## Decisión

Cada aplicación arranca un **trabajador en segundo plano** dentro de su propio proceso, al iniciar, y lo detiene al apagarse.

**Tienda** (`sincronizacion/trabajador.py`):

| Situación | Siguiente ciclo |
|---|---|
| Sincronizó bien | En `SYNC_INTERVALO_SEGUNDOS` (60 s) |
| Quedan eventos (el lote se llenó) | En 1 s, para vaciar rápido lo acumulado |
| Central no responde | Espera exponencial con margen aleatorio: hasta 1, 2, 4… s, con tope `SYNC_ESPERA_MAXIMA_SEGUNDOS` (300 s) |
| Central rechaza los eventos (conflicto) | En el intervalo normal; no se reintenta rápido porque reintentar no lo arregla |

- El margen aleatorio evita que, al volver central, todas las sucursales reintenten en el mismo instante.
- En conflicto se siguen recibiendo bajas y precios (decisión 0006), y el aviso queda visible en `GET /sincronizacion/estado` para que alguien lo revise.
- Una sola sincronización a la vez por proceso: el trabajador y el endpoint manual comparten un candado.
- Ningún error detiene al trabajador.

**Central** (`trabajador.py`): procesa un lote de la cola cada `CENTRAL_COLA_INTERVALO_SEGUNDOS` (5 s). Si el lote se llenó, sigue de inmediato; si PostgreSQL falla, reintenta con espera creciente. Su estado se consulta en `GET /sincronizacion/cola/trabajador`.

Ambos se pueden apagar con `SYNC_AUTOMATICA=false` y `CENTRAL_COLA_AUTOMATICA=false`; las pruebas lo hacen. Los endpoints manuales siguen disponibles.

## Consecuencias

**A favor**

- La sucursal se recupera sola después de una caída: se comprobó con procesos reales, apagando y encendiendo central.
- No hay que instalar ni operar un servicio aparte.

**En contra**

- Si se corre central con varios procesos (`--workers`), cada uno tendrá su trabajador. Es seguro, porque la cola usa `FOR UPDATE SKIP LOCKED`, pero hay más conexiones a PostgreSQL.
- Con varios procesos de la tienda, el candado no los coordina entre sí. En V1 hay una caja y un proceso por sucursal (decisión 0004).

## Alternativas consideradas

- **Un proceso aparte (cron o servicio del sistema):** separa responsabilidades, pero agrega instalación y configuración en cada tienda.
- **Sincronizar solo al hacer una venta:** no recupera la conexión si no hay ventas, y retrasa el cobro mientras se espera a central.

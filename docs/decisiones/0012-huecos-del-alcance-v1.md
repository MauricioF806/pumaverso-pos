# 0012. Lo que faltaba del alcance de V1: límite de tasa, auditoría y reporte de ventas

- **Estado:** Aceptada
- **Fecha:** 2026-11-28
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

Al revisar el PRD contra el código, tres puntos de la sección "Alcance V1 → Incluye" estaban incompletos:

1. **Límite de tasa por sucursal.** El PRD lo pide junto con el backoff y la cola intermedia como protección contra avalancha. La tienda ya se limitaba sola (lotes acotados, espera creciente con margen aleatorio), pero central no frenaba a una sucursal que pidiera demasiado.
2. **Cola de auditoría.** Central ya apartaba las operaciones de usuarios dados de baja, pero no había forma de consultarlas ni de marcarlas como revisadas sin entrar a la base de datos.
3. **Reporte de ventas para el gerente.** Las sospechas de inventario fantasma ya tenían reporte; de ventas solo había la lista de ventas recientes de la caja.

Al probar el reporte con la tienda real apareció además una falla que ya existía: **a veces el cobro respondía 500.** FastAPI abre la conexión SQLite de una petición en un hilo y ejecuta la operación en otro, y SQLite lo prohíbe por defecto. Casi siempre tocaba el mismo hilo; con la sincronización automática ocupando hilos, falló en la segunda venta.

## Decisión

### Límite de tasa (central)

- Cubeta de fichas por sucursal: ráfaga de 60 peticiones y recarga de 180 por minuto (`CENTRAL_LIMITE_RAFAGA`, `CENTRAL_LIMITE_POR_MINUTO`; 0 = sin límite).
- Aplica a toda petición con llave de sucursal. La llave de administración no tiene límite.
- Al pasarse: **429** con `Retry-After`. La tienda lo trata como falla pasajera: espera con su backoff y reintenta. No se pierde nada.
- Vive en la memoria del proceso de central; con un solo proceso (V1) basta. Con varios, cada uno llevaría su cuenta.

Una tienda con eventos pendientes sincroniza cada segundo (dos peticiones), así que 180 por minuto le alcanzan aun poniéndose al día.

### Cola de auditoría (central, solo administración)

- `GET /auditoria`: lo pendiente de revisar, con el usuario, el tipo de operación, su fecha y su total. Filtros: `sucursal_id` e `incluir_revisadas`.
- `POST /auditoria/{id}/revisar` con `revisada_por` y `nota`: deja constancia de quién revisó y qué concluyó. Un registro revisado no se vuelve a revisar.

### Reporte de ventas (tienda, gerente y supervisor)

- `GET /reportes/ventas?desde=…&hasta=…` (con zona horaria, máximo 31 días): ventas, total, piezas, ticket promedio, canceladas (aparte, no suman), ventas sin existencias registradas, desglose por cajero y los 5 productos más vendidos.
- Sale del registro de eventos de la tienda: **funciona sin conexión**.
- La pantalla calcula "hoy" en la hora local de la caja (de medianoche a medianoche) y lo muestra como un **corte del día**, con el mismo estilo de ticket térmico que el cobro.
- La pestaña del gerente pasa de llamarse **Inventario** a **Gerencia** (decisión 0011), porque ya no es solo inventario. Su primera sección es *Ventas del día*.

### Conexión SQLite entre hilos (tienda)

- `check_same_thread=False` al abrir la conexión. Es seguro porque cada conexión la usa una sola petición a la vez, aunque pase de un hilo a otro. Es la configuración que recomienda FastAPI para SQLite.

### Venta sin cobro confirmado tras un apagón (aclaración)

El PRD pide resolver "una venta sin cobro confirmado" al reiniciar. En este diseño ese estado no existe: el cobro **es** el evento de venta, y se guarda en una sola transacción con su efecto en el inventario (SQLite con `synchronous = FULL`). Tras un apagón la venta quedó completa o no existe. Procedimiento para el cajero: revisar **Ventas del turno**; si la venta aparece, quedó cobrada; si no, se cobra de nuevo.

## Consecuencias

**A favor**

- Quedan cubiertos los cinco puntos del alcance de V1 del PRD.
- El gerente ve cuánto vendió su tienda aunque no haya conexión.
- El cobro ya no falla al azar.

**En contra**

- El límite de tasa no se comparte entre varios procesos de central.
- El reporte de ventas es de una caja. Con varias cajas por sucursal (fuera de V1) habría que sumarlas.
- La revisión de auditoría solo se hace desde `/docs` de central; no tiene pantalla.

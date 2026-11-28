# 0009. Detección de inventario fantasma y cola aplicada de uno en uno

- **Estado:** Aceptada
- **Fecha:** 2026-11-27
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

El PRD (sección 5.3) define el inventario fantasma como la caída de las ventas de un SKU por debajo de su ritmo esperado en una sucursal y franja horaria: el sistema dice que hay piezas, pero no están en el anaquel. Pide que el umbral dependa de cada SKU y sucursal, no de un número fijo de días. Al sospechar, el SKU entra a la sugerencia de pedido, se avisa al gerente para que cuente y, si nadie cuenta, se escala al responsable regional. La métrica principal del proyecto es la venta perdida por fantasma.

Faltaba decidir cómo calcular el "ritmo esperado", qué tan baja debe ser una venta para sospechar y qué pasa con las sucursales que estuvieron sin conexión.

Al probar la demo con procesos reales apareció además una falla en la cola de central: si el trabajador automático y `POST /sincronizacion/procesar-cola` aplicaban eventos al mismo tiempo, actualizaban las mismas existencias en distinto orden y PostgreSQL los bloqueaba entre sí (*deadlock*). Como cada evento se aplica en su propio savepoint (decisión 0006), el error se quedaba en ese evento y lo apartaba como fallido aunque no tuviera nada malo. En la prueba se apartaron 129 ventas válidas.

## Decisión

### Detección

1. **Franjas de 4 horas** en la hora local de la sucursal (`America/Mexico_City`): 0-4, 4-8, 8-12, 12-16, 16-20 y 20-24.
2. **Ritmo esperado (λ):** promedio de piezas vendidas del SKU en la misma franja durante los últimos 28 días, contando solo los días en que la sucursal vendió algo en esa franja (si no vendió nada, estaba cerrada). Se necesitan al menos 7 días así; si no, no se evalúa. Las ventas canceladas no cuentan.
3. **Umbral por SKU y sucursal:** las ventas de una franja se modelan con una distribución de Poisson de media λ. Es sospecha si la probabilidad de vender esa cantidad o menos por azar es menor a 1 %. El umbral sale del ritmo de cada SKU: con λ = 6 solo vender 0 es sospechoso; con λ = 20, vender 9 o menos ya lo es; con λ = 0.5 nunca hay sospecha, porque no se distingue un anaquel vacío de un día flojo.
4. **No es fantasma si el sistema ya sabe que no hay:** con existencia registrada de 0 o menos no se sospecha.
5. **Solo con datos completos:** una franja se evalúa cuando central ya recibió, sin huecos, operaciones de la sucursal posteriores al fin de la franja. Una sucursal sin conexión no se confunde con una que dejó de vender: sus franjas se evalúan cuando vuelve. Se revisan las franjas terminadas de las últimas 24 horas y cada una se evalúa una sola vez (`inventario.franjas_evaluadas`).
6. **Corre sola** cada 15 minutos en central. También se puede pedir con `POST /inventario/fantasma/detectar` y, para demos y pruebas, con un `momento` distinto al actual.

Todos los valores se cambian con variables `CENTRAL_FANTASMA_*` en `.env`.

### Seguimiento de una sospecha

| Estado | Qué significa | Cómo se sale |
|---|---|---|
| `pendiente_conteo` | El gerente debe contar el anaquel | Conteo o ajuste; si pasan 24 h, se escala |
| `escalada` | Nadie contó a tiempo: le toca al responsable regional | Conteo o ajuste |
| `resuelta` | Se contó (`POST /inventario/fantasma/sospechas/{id}/conteo`) o la tienda envió un ajuste de ese SKU | — |

- No hay dos sospechas abiertas del mismo SKU en la misma sucursal.
- Mientras esté abierta, cada franja con faltante suma a la **venta perdida estimada**: (λ − vendidas) × precio.
- El conteo devuelve la diferencia contra lo registrado. El ajuste se hace en la tienda, que es la dueña del inventario físico (PRD 5.1); central no lo modifica.
- `GET /inventario/fantasma/sospechas` es el reporte del gerente: sospechas abiertas, SKUs para agregar al pedido y venta perdida estimada. En V1 es el único aviso al gerente (no hay notificaciones).

### Cola aplicada de uno en uno

7. **Solo un proceso aplica la cola a la vez**, con un candado de PostgreSQL (`pg_advisory_xact_lock`) al inicio de cada lote. El segundo espera a que el primero termine; los lotes son cortos.
8. **Los errores pasajeros de PostgreSQL no apartan eventos.** Un bloqueo mutuo, un conflicto de serialización o un candado no disponible deshacen el lote completo, que se repite en el siguiente ciclo. Solo se aparta un evento cuando el error es suyo.

## Consecuencias

**A favor**

- El umbral se explica en una frase ("vender tan poco por azar pasa menos de 1 de cada 100 veces") y se ajusta solo a cada producto y sucursal.
- Una sucursal sin conexión no genera sospechas falsas.
- La venta perdida estimada da la métrica principal del PRD.
- Basta SQL y Python; no hace falta pandas en V1.
- La cola ya no aparta ventas válidas cuando dos procesos coinciden.

**En contra**

- El promedio no distingue días de la semana: si un SKU se vende mucho más los sábados, un sábado flojo puede parecer normal y un lunes normal puede no detectar nada. Separar por día de la semana requiere más historia (pregunta abierta del PRD: cuántas semanas hay).
- Con 1 % de significancia y muchas franjas y productos, habrá algunas sospechas falsas. El conteo del gerente las cierra; si son demasiadas, se baja `CENTRAL_FANTASMA_SIGNIFICANCIA`.
- El plazo de 24 h para escalar es provisional: el PRD lo deja como pregunta abierta.
- Aplicar la cola de uno en uno limita cuánto se puede procesar en paralelo. Para V1 basta; si no alcanzara, se repartiría la cola por sucursal.

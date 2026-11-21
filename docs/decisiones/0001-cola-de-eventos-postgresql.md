# 0001. Cola de eventos en PostgreSQL

- **Estado:** Aceptada
- **Fecha:** 2026-11-21
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

Cuando varias sucursales recuperan la conexión al mismo tiempo, el servicio de Sincronización recibe muchos eventos de golpe. Se necesita una cola intermedia que reciba los eventos rápido y permita procesarlos después, sin saturar la base de datos central.

El PRD dejaba dos opciones para V1: Redis Streams o PostgreSQL. Kafka queda fuera del alcance.

## Decisión

La cola intermedia de V1 se implementa en **PostgreSQL**, en la misma instancia que ya usan los servicios centrales.

- El servicio de Sincronización guarda cada evento recibido en una tabla de cola dentro de su propio esquema.
- Los servicios que procesan eventos (Inventario y sus módulos de Proveedores y Auditoría) toman eventos pendientes de esa tabla y los marcan como procesados.
- Para que varios procesos no tomen el mismo evento, se usa `SELECT ... FOR UPDATE SKIP LOCKED`.

El diseño detallado de la tabla se define al iniciar el desarrollo de los servicios centrales.

## Consecuencias

**A favor**

- Una pieza menos que instalar, configurar y aprender: el equipo ya usará PostgreSQL.
- Guardar el evento y actualizar el último consecutivo recibido puede hacerse en una sola transacción, lo que simplifica la idempotencia.
- Para 20 sucursales, el volumen está muy por debajo de lo que PostgreSQL soporta como cola.

**En contra**

- La cola comparte recursos con la base de datos central.
- Si el sistema crece mucho más allá del piloto, habrá que revisar esta decisión.

## Alternativas consideradas

- **Redis Streams:** diseñado para colas, pero agrega un servicio más que instalar y operar, con comandos que el equipo no conoce. Se reconsiderará si el volumen lo justifica.
- **Kafka:** fuera del alcance de V1 por su complejidad de operación.

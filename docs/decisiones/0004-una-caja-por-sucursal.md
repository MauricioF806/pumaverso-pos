# 0004. Una caja por sucursal en V1

- **Estado:** Aceptada
- **Fecha:** 2026-11-21
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

La identidad de cada evento es **sucursal + caja + consecutivo**. Con varias cajas en una sucursal habría que coordinar consecutivos y accesos a la base de datos local entre equipos distintos, lo que agrega complejidad al piloto.

## Decisión

En V1, cada sucursal tiene **una sola caja**.

- El identificador de caja **se conserva** en la identidad de los eventos, aunque en V1 siempre tenga el mismo valor. Así, agregar más cajas en el futuro no cambia el formato de los eventos ni la lógica de idempotencia en central.
- Las sucursales del piloto deben elegirse entre las que operan con una sola caja.

## Consecuencias

**A favor**

- Un solo consecutivo por sucursal y una sola base de datos SQLite por tienda, sin acceso concurrente desde varios equipos.
- Menos casos de prueba en el piloto.

**En contra**

- El piloto no representa a las sucursales con más de una caja.
- Soportar varias cajas requerirá revisar cómo comparten inventario dentro de la misma tienda.

## Alternativas consideradas

- **Varias cajas desde V1:** representaría mejor la operación real, pero no cabe en el plazo de cuatro meses con cuatro personas.

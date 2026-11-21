# 0003. El contrato de eventos vive en `packages/contratos/`

- **Estado:** Aceptada
- **Fecha:** 2026-11-21
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

La tienda produce eventos (venta, cancelación, recepción de proveedor, merma, ajuste, traspaso) y central los recibe. Si cada lado define los eventos por su cuenta, tarde o temprano interpretarán un mismo evento de forma distinta, y eso rompe la sincronización.

## Decisión

Los modelos de los eventos se definen una sola vez en **`packages/contratos/`**, como un paquete de Python llamado `pumaverso_contratos`, usando Pydantic.

- `apps/tienda/` y `apps/central/` instalan este paquete y lo usan para crear y validar eventos.
- Cada evento incluye un número de versión del esquema, para poder cambiar el formato sin romper eventos que ya existen.
- Los tipos de TypeScript para la pantalla de caja se generan a partir del JSON Schema de los modelos, si la caja los necesita.
- Un cambio en `packages/contratos/` debe revisarse pensando en ambos lados: la tienda que produce el evento y central que lo recibe.

## Consecuencias

**A favor**

- Una sola definición de cada evento para todo el sistema.
- Pydantic valida automáticamente los eventos al crearlos y al recibirlos.
- Tienda y central pueden desarrollarse en paralelo una vez definido el contrato.

**En contra**

- Cambiar un evento afecta a dos aplicaciones a la vez y requiere más coordinación.

## Alternativas consideradas

- **Definir los modelos dentro de `apps/tienda/` y copiarlos a central:** más simple al inicio, pero las copias se desincronizan.

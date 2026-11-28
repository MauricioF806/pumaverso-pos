# 0006. Vigencia desde la recepción, sesiones con PIN y validación en central

- **Estado:** Aceptada
- **Fecha:** 2026-11-27
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

Una revisión de los pasos 3 al 6 encontró cuatro fallas, todas reproducidas con PostgreSQL y una tienda reales:

1. Las vigencias offline (24 h credenciales, 72 h precios, 30 días costos) se contaban desde la última vez que alguien **editó** el dato en central. Un cajero o un precio sin cambios en 4 días quedaban vencidos un segundo después de sincronizar.
2. Las operaciones aceptaban cualquier `usuario_id` sin comprobarlo, y `aprobado_por` podía ser cualquier texto. Se podía vender, cancelar o hacer mermas a nombre de otro, o con un usuario dado de baja.
3. Si central rechazaba un evento (por ejemplo, tras reinstalar una caja), la sincronización se detenía antes de descargar, así que las bajas de personal y los precios nunca llegaban.
4. Central no validaba el contenido ni el hash al recibir. Un evento malformado detenía toda la cola, incluidas las ventas legítimas de otras sucursales.

## Decisión

### Vigencia y sincronización (fallas 1 y 3)

- La vigencia de cada usuario y producto se cuenta **desde que la tienda lo recibe**. Por eso cada sincronización descarga los datos completos (`desde=None`).
- La tienda guarda el momento de la última descarga exitosa (`estado_bajada`). El reloj se revisa contra la más reciente entre esa descarga y la última confirmación de eventos, en **todas** las operaciones, no solo en ventas.
- Un ciclo de sincronización hace **primero la bajada y después la subida**. Si central rechaza los eventos (409), la tienda responde 409 indicando que los usuarios y precios sí se actualizaron. Un conflicto no se resuelve reintentando: requiere revisión.

### Identidad y aprobaciones (falla 2)

- `POST /auth/login` valida usuario y PIN y entrega un **token de sesión** que dura 12 horas. En la base solo se guarda el hash SHA-256 del token.
- Toda operación exige `Authorization: Bearer <token>` y se registra a nombre del usuario de la sesión. Lo que diga la petición se ignora.
- La sesión deja de servir en cuanto el usuario se da de baja o vencen sus credenciales locales.
- Cancelaciones y ajustes de inventario requieren `aprobador_id` y `aprobador_pin` de un usuario con rol **gerente** o **supervisor**, vigente y **distinto** de quien opera.
- Recepciones y mermas validan al usuario igual que una venta.

### Central (falla 4)

- El hash se calcula con una sola función en `packages/contratos` (`calcular_hash_evento`), que usan la tienda y central. El algoritmo no cambió: los eventos ya guardados siguen siendo válidos.
- Al recibir un lote, central valida la versión de esquema, el contenido de cada evento contra su modelo y el hash. Si un evento es inválido, rechaza el lote completo con 422.
- La cola aplica cada evento en su propio *savepoint*. Si uno falla, se marca como fallido con su error y la cola sigue. Los fallidos se consultan en `GET /sincronizacion/cola/fallidos`.

## Consecuencias

**A favor**

- La tienda funciona después de sincronizar, sin importar cuándo se editaron los datos en central.
- Cada evento tiene un responsable comprobado con PIN, y cada cancelación o ajuste, un aprobador comprobado.
- Un rechazo en la subida ya no deja a la tienda sin bajas de personal ni precios.
- Un evento malo ya no detiene el procesamiento de los demás.

**En contra**

- La pantalla de caja debe guardar el token de sesión y enviarlo en cada operación.
- Descargar todo en cada ciclo es más pesado. Para el piloto de 20 sucursales es aceptable.
- Un evento apartado como fallido deja el inventario de central incompleto hasta que alguien lo revise.

## Pendiente (fuera de esta decisión)

- ~~La API de central todavía no pide credenciales (revisión, falla 5).~~ Resuelto en la decisión 0010.
- La sincronización y la cola se ejecutan a mano con un endpoint; falta un proceso en segundo plano que use `calcular_espera_reintento()`.

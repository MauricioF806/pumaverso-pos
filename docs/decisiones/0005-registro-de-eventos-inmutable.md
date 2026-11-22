# 0005. Registro de eventos inmutable y estado de sincronización aparte

- **Estado:** Aceptada
- **Fecha:** 2026-11-21
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

El PRD exige que el registro de eventos sea *append-only*: un evento nunca se modifica ni se borra. Además, la sucursal necesita saber qué eventos ya recibió central para no enviarlos de nuevo.

Si esa marca de "ya sincronizado" se guardara en la fila de cada evento, habría que modificar eventos, lo que contradice el append-only.

## Decisión

1. **La base de datos obliga el append-only.** La tabla `eventos` tiene dos triggers que rechazan cualquier `UPDATE` o `DELETE`. La regla no depende de que cada integrante la recuerde.
2. **El avance de la sincronización vive en otra tabla.** `estado_sincronizacion` guarda, por sucursal y caja, el último consecutivo que central confirmó. Como central confirma sin huecos, ese número basta para saber qué falta enviar.
3. **El consecutivo se asigna dentro de la transacción de la operación**, con `BEGIN IMMEDIATE`. Si la operación falla, el número no se gasta y no quedan huecos.
4. **El hash SHA-256 cubre la identidad y todos los datos del evento**, calculado sobre JSON canónico (llaves ordenadas, sin espacios). Central lo usará para distinguir un reintento (mismo hash) de un conflicto (hash distinto).
5. **SQLite se configura para sobrevivir apagones:** `journal_mode = WAL` y `synchronous = FULL`.

## Consecuencias

**A favor**

- Ni un error de programación puede alterar el historial.
- Confirmar eventos no toca los eventos.
- Las pruebas demuestran que un proceso que muere a mitad de una venta no deja la venta a medias.

**En contra**

- Corregir un evento equivocado requiere un evento nuevo de ajuste o cancelación, nunca una edición.
- `synchronous = FULL` hace cada escritura un poco más lenta; para el volumen de una caja es imperceptible.

## Alternativas consideradas

- **Columna `sincronizado` en cada evento:** más simple, pero obliga a modificar eventos y rompe el append-only.
- **Confiar el append-only solo al código de Python:** cualquier consulta escrita a mano podría saltarse la regla.

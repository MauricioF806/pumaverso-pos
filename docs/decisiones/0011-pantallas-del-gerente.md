# 0011. Pantallas del gerente en la caja

- **Estado:** Aceptada
- **Fecha:** 2026-11-28
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

La API de la tienda ya permitía recibir mercancía, registrar mermas y hacer ajustes, y central ya detectaba inventario fantasma (decisión 0009). Pero no había pantallas: el gerente tenía que usar `/docs` o scripts. Además:

- El reporte de inventario fantasma vive en central, y la llave de administración no debe estar en la caja (decisión 0010).
- Los ajustes necesitan la aprobación de un gerente o supervisor distinto de quien opera (decisión 0006). En los datos de demostración solo había un gerente, así que un gerente no tenía quién le aprobara un ajuste.
- La simulación de inventario fantasma creaba una sucursal aparte (DEMO…), así que la pantalla del gerente de S001 se veía vacía.

## Decisión

1. **Pestaña Inventario en la misma pantalla de caja**, visible solo para los roles `gerente` y `supervisor`. La tienda vuelve a validar cada operación; ocultar la pestaña es solo para no estorbarle al cajero. Tiene cuatro secciones:

   | Sección | Qué hace | ¿Sin conexión? |
   |---|---|---|
   | Recibir mercancía | Proveedor, folio y, por producto, piezas y costo unitario de la factura | Sí |
   | Merma | Piezas y motivo (caducado, dañado u otro) | Sí |
   | Ajuste | Piezas contadas; la diferencia se calcula sola y la autoriza otra persona | Sí |
   | Inventario fantasma | Sospechas abiertas, venta perdida estimada y registro del conteo | No: el reporte lo calcula central |

2. **La tienda hace de intermediaria con central** para el inventario fantasma: `GET /inventario/fantasma` y `POST /inventario/fantasma/{id}/conteo`, solo para gerente o supervisor. Usa la llave de su sucursal, así que solo ve y cierra lo suyo. Sin conexión responde 503 con una explicación.
3. **La diferencia del conteo se calcula contra la existencia de la tienda, no la de central.** La tienda es la dueña de su inventario físico (PRD 5.1) y puede tener ventas que central aún no recibe. Tras el conteo, la pantalla ofrece el ajuste con el producto y las piezas ya capturados.
4. **Un usuario `supervisor1` en los datos de demostración**, para que el gerente pueda registrar ajustes y otra persona los autorice. La pantalla también avisa si alguien intenta autorizarse a sí mismo.
5. **La simulación de inventario fantasma ahora usa S001.** Su historial va a una caja aparte (`H` + mes y día) para no chocar con los consecutivos de la caja real; es solo para la demo. Para que funcione, central vuelve a revisar una franja que quedó *sin operación* o *con poca historia* mientras siga dentro de las últimas 24 horas, por si llega historia atrasada. Una franja ya evaluada con historia suficiente no se vuelve a evaluar (eso cambia lo que decía la decisión 0009: antes ninguna franja se reevaluaba).
6. **Las pruebas de la pantalla fallan si el HTML queda mal anidado.** Al probar en Chrome apareció un formulario dentro de otro: jsdom lo toleraba y en el navegador un Enter recargaba la página.

## Consecuencias

**A favor**

- El gerente opera la sucursal desde la caja, sin `/docs`.
- La demo de inventario fantasma se hace completa en la pantalla: ver la sospecha, contar y ajustar.
- La caja nunca tiene la llave de administración.

**En contra**

- La pestaña Inventario vive en la misma aplicación que el cobro. Si más adelante el gerente trabaja desde otra computadora, habría que separarla.
- En la demo, la existencia que central tiene de S001 incluye la mercancía simulada. No afecta a la tienda ni a la diferencia del conteo, que se calculan con la existencia local.
- Si el conteo ya se registró, la sospecha simulada no se puede repetir el mismo día: la franja ya quedó evaluada.

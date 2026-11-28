# 0010. Credenciales en la API de central

- **Estado:** Aceptada
- **Fecha:** 2026-11-28
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

La API de central no pedía ninguna credencial. Cualquiera que llegara a su dirección podía:

- crear un gerente con el PIN que quisiera (`PUT /usuarios`) y cambiar precios;
- enviar ventas a nombre de cualquier sucursal;
- descargar con `GET /sincronizacion/actualizaciones` el hash del PIN de todos los usuarios;
- cerrar sospechas de inventario fantasma y ver la cola.

La decisión 0006 lo dejó anotado como pendiente. Las sesiones con PIN de la tienda (también de la decisión 0006) protegen la API de la tienda, pero no la de central.

## Decisión

1. **Dos tipos de llave**, enviadas como `Authorization: Bearer <llave>`:

   | Llave | Dónde vive | Para qué sirve |
   |---|---|---|
   | De sucursal | `LLAVE_CENTRAL` en el `.env` de cada tienda | Sincronizar **su** sucursal, y ver y cerrar **sus** sospechas de inventario fantasma |
   | De administración | `CENTRAL_LLAVE_ADMIN` en el `.env` de central | Todo: usuarios, productos, cola, detección de fantasma y llaves de sucursal |

2. **Una sucursal no puede hacerse pasar por otra.** Con la llave de S001, pedir o enviar datos de S002 da 403. Una sospecha de otra sucursal responde 404, para no confirmar siquiera que existe.
3. **Central guarda solo el SHA-256 de cada llave de sucursal** (`sincronizacion.credenciales_sucursal`). Las llaves tienen al menos 32 caracteres; las que genera central son de 256 bits al azar, así que el SHA-256 sin sal basta: no se pueden adivinar por fuerza bruta. La llave de administración se compara en tiempo constante.
4. **Alta y cambio de llaves:**
   - `PUT /sucursales/{id}/llave` sin cuerpo: central genera una llave y la devuelve una sola vez.
   - Con `{"llave": "..."}`: registra esa llave (así lo hace el script de datos de demostración).
   - Registrar una llave nueva invalida la anterior.
   - `DELETE /sucursales/{id}/llave`: revoca la llave de esa sucursal.
   - Dos sucursales no pueden tener la misma llave.
5. **Sin `CENTRAL_LLAVE_ADMIN` nadie puede administrar central.** No hay llave por defecto en el código; el error lo explica. `.env.example` trae valores de desarrollo marcados para cambiarse.
6. **En la tienda**, si central rechaza la llave (401 o 403), la sincronización queda *en conflicto*, igual que con un conflicto de eventos (decisión 0007): no se reintenta rápido, la pantalla de caja pide avisar al gerente y el error dice que se revise `LLAVE_CENTRAL`. **Las ventas no se pierden:** siguen guardadas y se envían en cuanto la llave sea correcta.
7. `GET /health` sigue público. En `/docs`, el botón **Authorize** permite pegar una llave para probar.

## Consecuencias

**A favor**

- Sin llave, central no entrega datos ni acepta cambios.
- Si se filtra la llave de una sucursal, el daño se limita a esa sucursal y se corta con un cambio de llave.
- La operación sin conexión no cambia: la tienda solo necesita la llave para sincronizar, no para vender.

**En contra**

- La llave viaja en cada petición. Sin HTTPS, alguien en la misma red podría leerla. En el piloto, central debe publicarse detrás de HTTPS (por ejemplo, un proxy inverso con certificado). Está fuera del alcance de este cambio.
- La llave de administración es una sola y la comparte quien administre central. No hay usuarios de administración separados ni bitácora de quién hizo cada cambio; si hiciera falta, sería otra decisión.
- La llave de sucursal se configura a mano en el `.env` de cada tienda.

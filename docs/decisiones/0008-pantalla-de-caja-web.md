# 0008. Pantalla de caja como aplicación web; Tauri opcional

- **Estado:** Aceptada
- **Fecha:** 2026-11-27
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

El PRD proponía la pantalla de caja con TypeScript, React y Tauri. Tauri necesita instalar Rust y las herramientas de compilación de Visual Studio en cada computadora del equipo: varios GB y un lenguaje más, para un equipo con poco tiempo.

Además, para que una pantalla en el navegador pueda llamar a la API de la tienda, la API debe permitirlo (CORS). Y la pantalla necesitaba consultas que no existían: buscar productos, ver ventas recientes y saber quién tiene la sesión abierta.

## Decisión

1. **La pantalla de caja se hace como aplicación web con React y Vite**, en `apps/caja/`, y se abre en el navegador. Empaquetarla con Tauri queda como mejora opcional si sobra tiempo; el código de React sirve igual.
2. **La API de la tienda acepta llamadas del navegador solo desde los orígenes configurados** en `CORS_ORIGENES`. Por defecto, el servidor de desarrollo de Vite: `http://localhost:5173`. Solo se permiten `GET` y `POST`, con los encabezados `Authorization` y `Content-Type`.
3. **Consultas nuevas para la pantalla:**

| Endpoint | Para qué |
|---|---|
| `GET /auth/yo` | Saber quién tiene la sesión, para reanudarla al recargar |
| `GET /catalogo?buscar=` | Buscar productos por nombre o SKU cuando no hay código de barras; incluye existencia y si el precio está vigente |
| `GET /pos/ventas` | Últimas ventas de la caja, con su estado de cancelación |
| `GET /pos/ventas/{consecutivo}` | Detalle de una venta, para el ticket |

4. **Datos de demostración:** `scripts/cargar_datos_demo.py` carga usuarios y productos en central y deja existencias iniciales en la tienda. Se puede correr varias veces sin duplicar.

## Consecuencias

**A favor**

- El equipo solo necesita Node.js para la pantalla.
- Se prueba en el navegador con recarga automática al guardar, que es más rápido para desarrollar.
- La demo se prepara con un solo comando.

**En contra**

- Sin Tauri, la pantalla no es una aplicación instalable: se abre en el navegador de la caja.
- Si después se empaqueta con Tauri, hay que agregar sus orígenes a `CORS_ORIGENES`.

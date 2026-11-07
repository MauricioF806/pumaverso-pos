
# Pumaverso POS

Plataforma POS distribuida para una cadena de tiendas de conveniencia.

## Descripción

Pumaverso POS permite que cada sucursal opere de manera autónoma mediante un monolito local. El sistema registra ventas e inventario en SQLite y sincroniza sus operaciones con un servidor central cuando existe conexión.

## Arquitectura

- **Nodo local:** Python y FastAPI.
- **Base de datos local:** SQLite.
- **Interfaz de usuario:** TypeScript, React y Tauri.
- **Servidor central:** Microservicios con Python y FastAPI.
- **Base de datos central:** PostgreSQL.
- **Comunicación asíncrona:** Redis Streams.

## Principios de diseño

- Arquitectura local-first.
- Registro de eventos append-only.
- Transacciones locales atómicas.
- Sincronización idempotente.
- Autonomía de cada sucursal ante desconexiones.

## Estructura del proyecto

- `apps/tienda/`: monolito de cada sucursal.
- `docs/`: documentación técnica y arquitectónica.
- `scripts/`: herramientas de desarrollo.

## Desarrollo

El monolito utiliza Python 3.13.

Para preparar el entorno virtual:

```bash
py -3.13 -m venv .venv
source .venv/Scripts/activate
```

Las instrucciones de instalación y ejecución se documentarán conforme avance el desarrollo.

## Equipo

**Pumaverso Distribuido**

Proyecto académico de Sistemas Distribuidos.

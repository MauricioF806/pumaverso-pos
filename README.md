
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
- **Comunicación asíncrona:** cola de eventos en PostgreSQL.

## Principios de diseño

- Arquitectura local-first.
- Registro de eventos append-only.
- Transacciones locales atómicas.
- Sincronización idempotente.
- Autonomía de cada sucursal ante desconexiones.

## Estructura del proyecto

- `apps/tienda/`: monolito de cada sucursal.
- `apps/central/`: servicios centrales (planeada).
- `packages/contratos/`: contrato de eventos compartido entre tienda y central (planeada).
- `docs/`: documentación técnica y arquitectónica.
- `docs/decisiones/`: decisiones de arquitectura del equipo.
- `scripts/`: herramientas de desarrollo.

## Desarrollo

El monolito utiliza Python 3.13.

Para preparar el entorno virtual en Windows (Git Bash):

```bash
py -3.13 -m venv .venv
source .venv/Scripts/activate
```

En macOS o Linux:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
```

Instalar el monolito y sus dependencias de desarrollo (desde la raíz del repositorio):

```bash
python -m pip install -e "apps/tienda[dev]"
```

Crear el archivo de configuración local a partir del ejemplo:

```bash
cp .env.example .env
```

Levantar el servidor local:

```bash
python -m uvicorn pumaverso_tienda.main:app --reload
```

La API queda disponible en `http://127.0.0.1:8000`. La documentación interactiva está en `http://127.0.0.1:8000/docs` y la verificación de salud en `http://127.0.0.1:8000/health`.

Ejecutar las pruebas y el revisor de estilo:

```bash
python -m pytest
python -m ruff check apps/tienda
```

## Equipo

**Pumaverso Distribuido**

Proyecto académico de Sistemas Distribuidos.


# Pumaverso POS

Plataforma POS distribuida para una cadena de tiendas de conveniencia.

## Descripción

Pumaverso POS permite que cada sucursal opere de manera autónoma mediante un monolito local. El sistema registra ventas e inventario en SQLite y sincroniza sus operaciones con un servidor central cuando existe conexión.

## Arquitectura

- **Nodo local:** Python y FastAPI.
- **Base de datos local:** SQLite.
- **Pantalla de caja:** aplicación web con TypeScript, React y Vite (Tauri opcional, decisión 0008).
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
- `apps/central/`: servicios centrales.
- `packages/contratos/`: contrato de eventos compartido entre tienda y central.
- `apps/caja/`: pantalla de caja (React + Vite).
- `docs/`: documentación técnica y arquitectónica.
- `docs/decisiones/`: decisiones de arquitectura del equipo.
- `scripts/`: herramientas de desarrollo.

## Desarrollo

El proyecto utiliza Python 3.13. El servidor central necesita además PostgreSQL, que se levanta con Docker.

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

Instalar los tres paquetes y sus dependencias de desarrollo (desde la raíz del repositorio). El contrato va primero, porque la tienda y central dependen de él y no está publicado en PyPI:

```bash
python -m pip install -e "packages/contratos[dev]" -e "apps/tienda[dev]" -e "apps/central[dev]"
```

Crear el archivo de configuración local a partir del ejemplo:

```bash
cp .env.example .env
```

Si ya tenías un `.env`, agrégale `CENTRAL_LLAVE_ADMIN` y `LLAVE_CENTRAL` desde `.env.example` (ver [Credenciales](#credenciales)).

Levantar PostgreSQL y el servidor central (puerto 8001):

```bash
docker compose up -d
python -m uvicorn pumaverso_central.main:app --reload --port 8001
```

Levantar el nodo de la sucursal (puerto 8000), en otra terminal:

```bash
python -m uvicorn pumaverso_tienda.main:app --reload
```

La documentación interactiva de cada uno está en `/docs` (`http://127.0.0.1:8000/docs` y `http://127.0.0.1:8001/docs`).

La tienda sincroniza sola con central cada 60 segundos y reintenta con espera creciente si central no responde. Central procesa su cola cada 5 segundos (decisión 0007). Para ver cómo va:

- Tienda: `GET /sincronizacion/estado`
- Central: `GET /sincronizacion/cola/trabajador` y `GET /sincronizacion/cola/fallidos`

Los intervalos se cambian en `.env`. Para una demo conviene bajarlos, por ejemplo `SYNC_INTERVALO_SEGUNDOS=5`.

### Credenciales

La API de central pide una llave en el encabezado `Authorization: Bearer <llave>` (decisión 0010). Solo `/health` es pública.

- **Llave de administración** (`CENTRAL_LLAVE_ADMIN` en el `.env` de central): sirve para todo, incluido dar de alta usuarios, productos y llaves de sucursal.
- **Llave de sucursal** (`LLAVE_CENTRAL` en el `.env` de la tienda): solo sirve para sincronizar esa sucursal y ver sus sospechas de inventario fantasma.

En desarrollo, `scripts/cargar_datos_demo.py` registra en central la llave de `LLAVE_CENTRAL`. Para una sucursal nueva, el administrador llama `PUT /sucursales/{id}/llave` sin cuerpo; central devuelve una llave al azar una sola vez y guarda solo su SHA-256. En `/docs` de central, el botón **Authorize** permite pegar una llave para probar.

Si la tienda dice que central rechazó su llave, revisa que `LLAVE_CENTRAL` sea la registrada para su `SUCURSAL_ID`. Las ventas no se pierden: se envían en cuanto la llave es correcta.

### Pantalla de caja

Necesita Node.js 20 o más nuevo. Con la tienda levantada, en otra terminal:

```bash
cd apps/caja
npm install
npm run dev
```

Abre `http://localhost:5173` y entra con un usuario de la demo (por ejemplo `cajero1` / `1234`). Más detalles en [`apps/caja/README.md`](apps/caja/README.md).

### Datos de demostración

Con central y la tienda levantados, este comando (desde la raíz del repo, porque lee las llaves del `.env`) carga 3 usuarios y 12 productos en central, registra la llave de la sucursal, sincroniza la tienda y deja 24 piezas de cada producto:

```bash
python scripts/cargar_datos_demo.py
```

Al terminar muestra los usuarios y sus PIN (`cajero1` / `1234`, `gerente1` / `9999`…). Se puede correr varias veces sin duplicar datos. Con `--help` se ven las opciones.

### Inventario fantasma

Central busca productos que dejaron de venderse aunque el sistema dice que hay: probablemente no están en el anaquel (PRD 5.3, decisión 0009). Lo revisa solo cada 15 minutos.

- `GET /inventario/fantasma/sospechas`: reporte para el gerente, con qué contar, qué agregar al pedido y la venta perdida estimada.
- `POST /inventario/fantasma/sospechas/{id}/conteo`: registra el conteo físico y cierra la sospecha.
- `POST /inventario/fantasma/detectar`: revisa en ese momento.

Para verlo en acción, con central levantado y los datos de demostración cargados:

```bash
python scripts/simular_fantasma.py
```

Crea una sucursal simulada con 4 semanas de ventas en la que ayer la leche dejó de venderse, y muestra la sospecha.

### Uso de la API de la tienda

Las operaciones requieren una sesión (decisión 0006):

1. `POST /auth/login` con `usuario_id` y `pin` devuelve un `token`.
2. Cada operación lleva el encabezado `Authorization: Bearer <token>` y queda a nombre de ese usuario.
3. Cancelaciones y ajustes piden además `aprobador_id` y `aprobador_pin` de un gerente o supervisor distinto de quien opera.

Consultas pensadas para la pantalla de caja (decisión 0008): `GET /auth/yo`, `GET /catalogo?buscar=`, `GET /pos/ventas` y `GET /pos/ventas/{consecutivo}`. La API acepta llamadas del navegador solo desde los orígenes de `CORS_ORIGENES` (por defecto, Vite en `http://localhost:5173`).

### Pruebas

```bash
python -m pytest
python -m ruff check apps packages
python -m ruff check scripts --line-length 100
cd apps/caja && npm test   # pruebas de la pantalla de caja
```

`apps/central/tests/test_punta_a_punta.py` levanta la tienda y central reales y prueba las fallas de la demo: vender sin central, reconectar y perder la respuesta de central sin duplicar ventas.

Las pruebas de central necesitan PostgreSQL (`docker compose up -d`). Usan su propia base, `pumaverso_central_test`, que se crea sola; nunca tocan la de desarrollo. Si PostgreSQL no está disponible, esas pruebas se saltan con un aviso.

## Equipo

**Pumaverso Distribuido**

Proyecto académico de Sistemas Distribuidos.

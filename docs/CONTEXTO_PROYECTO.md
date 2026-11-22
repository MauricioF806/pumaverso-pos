# Contexto del proyecto — Pumaverso POS

**Equipo:** Pumaverso Distribuido  
**Proyecto:** Plataforma POS distribuida para una cadena de tiendas de conveniencia  
**Curso:** Sistemas Distribuidos  
**Versión del documento:** 1.2

---

## 1. Descripción general

Pumaverso POS es una plataforma de punto de venta (POS, *Point of Sale*) distribuida, diseñada para una cadena de tiendas de conveniencia.

Su objetivo principal es permitir que cada sucursal opere de manera autónoma, incluso cuando pierde la conexión con el servidor central. Las operaciones realizadas localmente deben conservarse y sincronizarse posteriormente, sin perder ni duplicar transacciones.

El sistema sigue un enfoque **local-first**: cada sucursal registra sus operaciones en su propio nodo y la sincronización con el servidor central ocurre en segundo plano cuando existe conectividad.

### Objetivo de la primera versión (V1)

Implementar un piloto de 20 sucursales que permita:

- Realizar ventas sin conexión a Internet.
- Registrar operaciones localmente.
- Mantener la consistencia del inventario.
- Registrar eventos de manera auditable.
- Sincronizar información entre sucursales y el servidor central.
- Evitar la pérdida y duplicación de transacciones.

**Restricciones del proyecto:** cuatro desarrolladores y cuatro meses de desarrollo.

---

## 2. Principios de diseño

Todas las decisiones de implementación deben respetar los siguientes principios:

### 2.1. Local-first

Cada sucursal debe poder realizar las operaciones permitidas sin depender de la disponibilidad del servidor central.

Las operaciones se registran primero en la base de datos local y posteriormente se sincronizan.

### 2.2. Append-only

El historial de operaciones no debe modificarse ni eliminarse para corregir errores.

Las correcciones deben registrarse como eventos nuevos de ajuste, incluyendo el motivo y el aprobador correspondiente.

### 2.3. Operaciones offline completas

Solo se permiten operaciones que puedan completarse con los recursos disponibles localmente.

No se deben iniciar operaciones que dependan de servicios externos cuando la sucursal está desconectada.

### 2.4. Transacciones locales atómicas

Las operaciones que involucren varios módulos deben ejecutarse dentro de una misma transacción local cuando corresponda.

Por ejemplo, una venta debe mantener la consistencia entre el registro de la venta, el inventario y los datos del usuario.

### 2.5. Sincronización idempotente

La recepción repetida de un mismo evento no debe generar operaciones duplicadas.

El sistema debe reconocer los eventos previamente procesados.

### 2.6. Propiedad de los datos

Cada tipo de información tiene un responsable principal (*owner*). Los servicios o nodos que no son propietarios pueden consultar, proponer o conciliar cambios, pero no modificar directamente la información que pertenece a otro responsable.

---

## 3. Alcance funcional de V1

### 3.1. Operaciones permitidas sin conexión

| Operación | Disponibilidad offline |
|---|---|
| Venta de productos | Permitida |
| Recepción de mercancía de proveedores | Permitida |
| Recargas telefónicas | No permitida |
| Pago de servicios | No permitido |
| Pago con tarjeta | No permitido |

Una venta requiere que el producto exista físicamente y que su precio almacenado localmente siga vigente.

### 3.2. Vigencia de la información (TTL)

| Información | TTL | Comportamiento al caducar |
|---|---:|---|
| Credenciales de usuarios | 24 horas | No se permite iniciar sesión |
| Precios y promociones | 72 horas | Se bloquea la venta del SKU afectado |
| Costos | 30 días | Se marca para revisión, sin bloquear la venta |

Las alertas sanitarias y de ley seca no cuentan con un mecanismo offline en V1. Se manejarán mediante un proceso manual.

### 3.3. Punto de confirmación de una venta

Una venta existe desde el momento en que se registra el cobro en el log local, antes de imprimir o entregar el ticket.

- Si ocurre un apagón antes del registro del cobro, la venta no existe.
- Si ocurre después, la venta se considera completa.
- Si al reiniciar existe una venta sin confirmación de cobro, debe resolverse mediante un evento auditado que identifique al usuario responsable de la decisión.

### 3.4. Identidad de las operaciones

La identidad de una operación no depende del reloj.

Se utiliza la combinación:

**Sucursal + caja + consecutivo**

En V1 cada sucursal opera con una sola caja, pero el identificador de caja se conserva en la identidad para no cambiar el formato cuando se agreguen más cajas (ver `docs/decisiones/0004-una-caja-por-sucursal.md`).

El reloj se utiliza para reportes y referencias temporales. Si la fecha local es anterior al último momento de sincronización registrado, se bloquean las ventas hasta corregirla.

---

## 4. Arquitectura del sistema

El sistema se divide en dos partes principales: el nodo local de cada sucursal y los servicios centrales.

### 4.1. Nodo local de sucursal

Cada sucursal ejecuta un **monolito modular** con una base de datos SQLite.

La decisión de utilizar un monolito local responde a la necesidad de mantener las operaciones atómicas y garantizar que la sucursal pueda funcionar sin conexión.

Separar los módulos locales en microservicios independientes complicaría la coordinación de transacciones y la operación offline.

Tecnologías:

- Python 3.13.
- FastAPI.
- SQLite.
- TypeScript.
- React.
- Tauri.
- PyInstaller para empaquetar el servicio local.

### 4.2. Módulos del monolito local

| Módulo | Responsabilidad |
|---|---|
| `pos/` | Registro y procesamiento de ventas |
| `inventario/` | Existencias, movimientos y recepción de mercancía |
| `usuarios/` | Usuarios, autenticación y permisos locales |
| `catalogo/` | Productos, precios y promociones |
| `eventos/` | Registro de eventos y consecutivos |
| `sincronizacion/` | Envío y recepción de información con central |
| `persistencia/` | Conexiones, transacciones y acceso a SQLite |
| `api/` | Endpoints y comunicación con la interfaz y otros componentes |

Los módulos deben mantener responsabilidades separadas, pero compartir la infraestructura local necesaria para garantizar la atomicidad de las operaciones.

### 4.3. Servicios centrales

La arquitectura central contempla los siguientes servicios:

- Sincronización.
- Usuarios.
- Catálogo y precios.
- Inventario.
- Proveedores.
- Auditoría.
- Pagos y recargas (fuera del alcance offline de V1).

Por las restricciones del equipo y del plazo, Proveedores y Auditoría comenzarán como módulos dentro del servicio de Inventario. Podrán separarse posteriormente si resulta necesario.

Tecnologías:

- Python y FastAPI.
- PostgreSQL.
- PostgreSQL como cola de eventos para la comunicación asíncrona (ver `docs/decisiones/0001-cola-de-eventos-postgresql.md`).
- SQL y Python/pandas para detección de inventario fantasma.

Cada servicio central tendrá su propio esquema en PostgreSQL, aunque inicialmente se utilice una misma instancia de base de datos.

### 4.4. Comunicación entre componentes

El nodo local se comunica con central mediante dos canales:

1. **Subida:** envío de eventos locales hacia el servicio de Sincronización.
2. **Bajada:** recepción de información central, como credenciales, bajas, precios y costos.

La comunicación entre servicios centrales se realiza mediante eventos asíncronos, a través de una cola de eventos en PostgreSQL.

Ningún servicio debe depender de una llamada síncrona a otro para completar el registro local de una venta.

---

## 5. Inventario y consistencia

### 5.1. Propiedad de los datos

| Información | Responsable principal |
|---|---|
| Ventas | Nodo local |
| Inventario físico | Nodo local |
| Traspasos entre sucursales | Ambos nodos involucrados |
| Usuarios y permisos | Central |
| Precios y costos | Central |

Central consolida las ventas y no modifica su historial. En inventario, central propone movimientos y realiza conciliaciones.

### 5.2. Validación de existencias

El POS no debe bloquear una venta únicamente porque el inventario indique existencias insuficientes.

Si el producto se encuentra físicamente disponible en el mostrador, se permite venderlo.

Cuando una venta produce inventario negativo:

- Se genera automáticamente un evento marcado.
- Más de dos eventos de inventario negativo en 24 horas en la misma caja generan una alerta.

### 5.3. Inventario fantasma

Se considera inventario fantasma cuando las ventas de un SKU caen por debajo del ritmo esperado para una sucursal y franja horaria.

El umbral se determina por SKU y sucursal, no mediante una cantidad fija de días.

Cuando se detecta una sospecha:

1. Se asume que el inventario disponible podría ser menor al registrado.
2. El SKU se incluye en la sugerencia de pedido.
3. Se notifica al gerente para realizar un conteo físico.
4. Si no se realiza el conteo, el caso se escala al responsable regional.

V1 no contempla resurtido automático.

---

## 6. Eventos y sincronización

### 6.1. Registro de eventos

Cada nodo mantiene un único registro de eventos con un solo consecutivo.

Los tipos de eventos contemplados incluyen:

- Venta.
- Cancelación.
- Recepción de proveedor.
- Merma.
- Ajuste.
- Traspaso.

Las cancelaciones y los ajustes se registran como eventos nuevos. No se elimina ni modifica el evento original.

### 6.2. Identidad de eventos

La identidad de cada evento se construye mediante:

**Sucursal + caja + consecutivo + hash del contenido**

El servidor central conserva el último consecutivo recibido por cada caja.

Al recibir eventos:

- Si el evento ya fue recibido, se descarta el reintento.
- Si existe un hueco en los consecutivos, se conservan los eventos recibidos y se solicita el faltante.
- Los eventos deben poder procesarse de manera idempotente.

### 6.3. Control de sincronización

Para evitar saturar el servidor central cuando varias sucursales recuperan conexión:

- Se establece un límite de tasa por sucursal.
- Se utilizan reintentos con backoff exponencial y jitter.
- Se emplea una cola intermedia para regular el flujo de eventos.

Para V1, la cola intermedia se implementa en PostgreSQL (ver `docs/decisiones/0001-cola-de-eventos-postgresql.md`). Redis Streams se reconsiderará solo si el volumen lo justifica.

Kafka queda fuera del alcance inicial.

### 6.4. Bajas de usuarios

Si un usuario es dado de baja mientras una sucursal está desconectada, existe una ventana máxima de exposición de 24 horas.

Las transacciones realizadas por un usuario revocado durante ese periodo se envían a una cola de auditoría al sincronizar.

---

## 7. Estructura actual del repositorio

La estructura del proyecto es la siguiente. Las carpetas marcadas como *planeada* todavía no existen y se crearán al desarrollar esa parte:

```text
pumaverso-pos/
├── apps/
│   ├── tienda/                  # monolito de cada sucursal
│   │   ├── src/
│   │   │   └── pumaverso_tienda/
│   │   │       ├── api/
│   │   │       │   └── routes/
│   │   │       ├── catalogo/
│   │   │       ├── eventos/
│   │   │       ├── inventario/
│   │   │       ├── persistencia/
│   │   │       ├── pos/
│   │   │       ├── sincronizacion/
│   │   │       ├── usuarios/
│   │   │       ├── config.py
│   │   │       └── main.py
│   │   ├── tests/
│   │   └── pyproject.toml
│   └── central/                 # servicios centrales (planeada, decisión 0002)
├── packages/
│   └── contratos/               # contrato de eventos compartido (planeada, decisión 0003)
├── docs/
│   ├── decisiones/              # decisiones de arquitectura del equipo
│   ├── arquitectura.md
│   └── CONTEXTO_PROYECTO.md
├── scripts/
├── .env.example
├── .gitignore
├── CONTRIBUTING.md
└── README.md
```

Puede ampliarse conforme avance el desarrollo, siempre que los cambios respeten la arquitectura acordada.

---

## 8. Reglas de desarrollo para integrantes e IA

Cualquier persona o herramienta de IA que contribuya al proyecto debe respetar las siguientes reglas:

1. Consultar este documento y el README antes de proponer cambios.
2. Respetar el PRD y las decisiones arquitectónicas documentadas.
3. No convertir el monolito local en microservicios independientes.
4. No introducir tecnologías o dependencias sin justificar su necesidad.
5. Mantener separadas las responsabilidades de cada módulo.
6. Revisar las interfaces existentes antes de modificar archivos compartidos.
7. Mantener la atomicidad de las operaciones locales.
8. Respetar el principio append-only en el registro de eventos.
9. Garantizar idempotencia en los procesos de sincronización.
10. No inventar funciones, clases, archivos o interfaces que no existan.
11. Indicar qué archivos se modifican y explicar el propósito de cada cambio.
12. Proponer pruebas para validar el comportamiento implementado.
13. No sobrescribir cambios realizados por otros integrantes.
14. Si existe una contradicción o falta información, preguntar antes de asumir.
15. Documentar las decisiones técnicas que cambien o amplíen la arquitectura.

### Instrucción especial para herramientas de IA

Antes de generar o modificar código, analiza el contexto del proyecto y revisa los archivos relacionados con la tarea.

No debes asumir que una solución técnicamente posible es compatible con Pumaverso POS. Prioriza las decisiones del equipo, el alcance de V1 y la operación offline.

Cuando propongas una solución:

- Explica brevemente qué problema resuelve.
- Identifica los archivos que deben crearse o modificarse.
- Respeta las interfaces y convenciones existentes.
- Explica cómo ejecutar y probar los cambios.
- Señala cualquier decisión que requiera aprobación del equipo.

No realices cambios arquitectónicos por iniciativa propia.

---

## 9. Aspectos pendientes de definición

Los siguientes puntos todavía requieren una decisión explícita del equipo:

- Determinar si los pagos online se incluirán en V1.
- Confirmar qué sucursales participan en el piloto. Deben operar con una sola caja (ver decisión 0004).
- Establecer cuánto tiempo puede transcurrir antes de escalar un conteo físico pendiente.
- Definir la fuente y el periodo de referencia para el historial de ventas.
- Establecer quién puede aprobar ajustes de inventario.
- Precisar el procedimiento para resolver ventas cuyo cobro no pudo confirmarse tras un apagón.

Estas decisiones no deben asumirse como definitivas hasta que el equipo las acuerde y documente.

Las decisiones ya tomadas se registran en `docs/decisiones/`, una por archivo y numeradas en orden.

---

## 10. Estado actual del desarrollo

El repositorio cuenta con una estructura inicial para el monolito local de cada sucursal.

Hasta la elaboración de este documento:

- Se configuró el repositorio Git.
- Se creó la estructura modular inicial.
- Se configuró Python 3.13 mediante un entorno virtual.
- Se definió `pyproject.toml` con las dependencias iniciales.
- Se prepararon los archivos básicos de documentación y configuración.
- Se corrigió el formato de la documentación.
- Se documentaron las primeras cuatro decisiones de arquitectura en `docs/decisiones/`.
- Se implementaron los módulos `persistencia` (conexión SQLite, transacciones y migraciones) y `eventos` (registro append-only, consecutivo sin huecos, hash y estado de sincronización), con sus pruebas. Ver decisión 0005.

La implementación funcional de los demás módulos todavía está pendiente.

**Próximo objetivo:** definir el contrato de eventos en `packages/contratos/` (decisión 0003) y mover ahí los tipos de evento que hoy viven en `eventos/registro.py`.

# Pantalla de caja

Interfaz que usa el cajero: iniciar sesión, cobrar y ver las ventas del turno. Es una aplicación web con TypeScript, React y Vite que habla con la API de la tienda (`apps/tienda`). Ver la decisión 0008.

## Requisitos

- Node.js 20 o más nuevo (`node --version`).
- La tienda corriendo en `http://127.0.0.1:8000` (ver el README principal).

## Uso

Desde esta carpeta:

```bash
npm install
npm run dev
```

Abre `http://localhost:5173`. Para tener usuarios y productos, corre antes `python scripts/cargar_datos_demo.py` desde la raíz del repositorio; muestra los usuarios y sus PIN al terminar.

Si la tienda está en otra dirección, copia `.env.example` como `.env.local` y cambia `VITE_API_URL`.

## Qué hace

| Pantalla | Qué puede hacer el cajero |
|---|---|
| Abrir caja | Entrar con usuario y PIN. Si recarga la página, la sesión se reanuda sola. |
| Cobrar | Escanear o escribir el código (Enter agrega), buscar por nombre, ajustar cantidades, capturar con cuánto paga y cobrar con el botón o con **F12**. El cambio queda a la vista al terminar. |
| Ventas del turno | Ver las últimas ventas y cancelar una con el usuario y PIN de un gerente o supervisor. |
| Barra superior | Qué caja es, quién atiende y el estado de la conexión con central. |

El indicador de conexión consulta a la tienda cada 5 segundos:

- **En línea:** todo bien; lo pendiente se envía solo.
- **Sin conexión con central:** se puede seguir vendiendo; muestra cuántas operaciones faltan por enviar.
- **Central rechazó las ventas:** hay un conflicto que debe revisar el gerente.
- **Sin servicio local:** la tienda no responde y no se puede cobrar.

## Pruebas

```bash
npm test           # pruebas de la interfaz, con la API simulada
npm run typecheck  # revisión de tipos de TypeScript
npm run build      # versión para producción en dist/
```

## Organización del código

```text
src/
├── api.ts                  # llamadas a la API de la tienda y sus errores
├── tipos.ts                # forma de las respuestas de la API
├── dinero.ts               # centavos → "$21.50"
├── sesion.ts               # guarda el token de sesión en el navegador
├── App.tsx                 # inicio de sesión o caja abierta
├── estilos.css
├── componentes/
│   ├── InicioSesion.tsx
│   ├── Cobro.tsx           # escáner, búsqueda, pago y cobro
│   ├── Ticket.tsx          # el carrito, con forma de ticket térmico
│   ├── VentasDelTurno.tsx
│   ├── DialogoCancelacion.tsx
│   └── EstadoConexion.tsx
└── pruebas/
```

Las fuentes (Lexend e IBM Plex Mono) se instalan como paquetes y viajan con la aplicación: la caja no necesita internet para verse bien.

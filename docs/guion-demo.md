# Guion de la demo

Demo de unos 12 minutos con tres partes:

1. La caja vende normal.
2. Central se cae, la caja sigue vendiendo y, al volver, envía todo sin perder ni duplicar.
3. Central detecta inventario fantasma en otra sucursal.

Cada paso dice qué hacer, qué decir y qué debe verse.

## Antes de la demo (15 minutos antes)

1. Abre **Docker Desktop** y espera a que diga que está corriendo.
2. En tu `.env` (raíz del repo) deja la sincronización rápida para que se note en vivo:

   ```
   SYNC_INTERVALO_SEGUNDOS=5
   SYNC_ESPERA_MAXIMA_SEGUNDOS=10
   ```

3. Abre cuatro terminales. En cada una: `cd ~/Documents/pumaverso-pos` y `source .venv/Scripts/activate`.

   | Terminal | Comando |
   |---|---|
   | 1, central | `docker compose up -d` y luego `python -m uvicorn pumaverso_central.main:app --port 8001` |
   | 2, tienda | `python -m uvicorn pumaverso_tienda.main:app` |
   | 3, comandos | `python scripts/cargar_datos_demo.py` |
   | 4, pantalla | `cd apps/caja` y `npm run dev` |

   Sin `--reload`: así Ctrl+C apaga central de inmediato.

4. Abre http://localhost:5173 en el navegador y entra con `cajero1` / `1234`. Debe decir **En línea**.
5. Abre otra pestaña con http://127.0.0.1:8001/docs (la API de central).
6. Ensaya la parte 3 una vez: corre `python scripts/simular_fantasma.py` en la terminal 3. Correrlo otra vez el mismo día no duplica nada. Si en el ensayo registras el conteo, la sospecha queda cerrada: en la demo usa otra sucursal con `--sucursal DEMO2`.

Acomoda la pantalla para que se vean el navegador y la terminal 1 al mismo tiempo.

## Parte 1. Una venta normal (2 min)

**Haz:**

1. Escanea `7501055300846` (refresco) dos veces y `7501020515343` (leche).
2. En **Paga con** escribe `100` y presiona **F12**.

**Debe verse:** el ticket con 3 piezas por $72.00 y luego "Venta N cobrada" con **Cambio: $28.00**. En la terminal 1 aparece `POST /sincronizacion/eventos ... 200 OK`.

**Di:** "La venta se guarda primero en la base de datos de la sucursal, y en ese momento ya está cobrada. Unos segundos después la tienda la envía sola a central. El cajero no espera a central para nada."

## Parte 2. Central se cae (4 min)

**Haz:**

1. En la terminal 1, presiona **Ctrl+C**. Central se apaga.
2. Espera unos segundos: el indicador cambia a **Sin conexión con central**.
3. Cobra otras dos ventas (por ejemplo, un refresco con `50` y una leche con `50`).

**Debe verse:** las ventas se cobran igual y el indicador dice **2 operaciones por enviar**.

**Di:** "Esta es la regla principal del PRD: la sucursal nunca deja de vender porque central no responda. Cada venta lleva sucursal, caja, un consecutivo sin huecos y un hash de su contenido. Con eso central sabe si le falta algo y si alguien lo modificó."

4. Levanta central otra vez (flecha ↑ y Enter en la terminal 1).

**Debe verse:** en menos de 15 segundos, en la terminal 1 primero un `GET /sincronizacion/actualizaciones` y después un `POST /sincronizacion/eventos`. El indicador regresa a **En línea**.

**Di:** "Al volver, la tienda primero baja precios y usuarios y después sube las ventas. Si central rechazara algo, la sucursal no se queda sin recibir bajas de personal ni precios nuevos."

5. Abre **Ventas del turno**: ahí están las ventas, incluidas las que se hicieron sin conexión.

### ¿Y si la respuesta de central se pierde?

**Haz:** en la terminal 3, corre:

```bash
python -m pytest apps/central/tests/test_punta_a_punta.py -v
```

**Debe verse:** 3 pruebas en verde.

**Di:** "Hay un caso que no se puede mostrar desconectando un cable: central recibe las ventas, pero su respuesta se pierde en la red. La tienda cree que no llegaron y las vuelve a enviar. Esta prueba levanta la tienda y central reales con PostgreSQL y simula justo eso. Central reconoce cada venta por sucursal, caja y consecutivo y no la cuenta dos veces: el inventario se descuenta una sola vez."

## Parte 3. Inventario fantasma (5 min)

**Di primero:** "La métrica principal del PRD no es técnica: es la venta perdida por inventario fantasma. Pasa cuando el sistema dice que hay producto, pero no está en el anaquel; por ejemplo, alguien lo dejó en la bodega. Nadie lo resurte porque el sistema cree que hay, y se deja de vender."

**Haz:** en la terminal 3:

```bash
python scripts/simular_fantasma.py
```

**Debe verse** algo así:

```
Sucursal simulada DEMO1127: 28 días de historia, 2608 operaciones enviadas a central.
Revisión de ayer (26/11) a las 20:30:

  Sospecha de inventario fantasma: Leche entera 1 L
    Entre 12:00 y 16:00 se esperaban unas 5.6 piezas y se vendieron 0.
    Probabilidad de que sea casualidad: 0.37 %
    El sistema dice que hay 1499 piezas.
    Estado: el gerente debe contar el anaquel

  Agregar al pedido: Leche entera 1 L
  Venta perdida estimada: $372.85
```

**Di:** "Simulamos otra sucursal con cuatro semanas de ventas. Ayer, desde el mediodía, la leche dejó de venderse aunque el sistema dice que hay casi 1,500 piezas. Central calcula el ritmo normal de cada producto en cada franja de 4 horas: la leche se vende unas 6 veces entre 12 y 4. Con ese ritmo, vender 0 por pura casualidad pasa menos de 1 de cada 100 veces, así que lo marca como sospecha. El umbral sale del ritmo de cada producto y cada sucursal, como pide el PRD, no de un número fijo de días."

**Haz:** en la pestaña de `/docs`:

1. Abre `GET /inventario/fantasma/sospechas` → **Try it out** → **Execute**. Muestra `sugerencia_pedido` y `venta_perdida_estimada_centavos`.
2. Copia el `id` de la sospecha.
3. Abre `POST /inventario/fantasma/sospechas/{sospecha_id}/conteo` → **Try it out**. Pega el `id` y en el cuerpo escribe:

   ```json
   {"cantidad_contada": 0, "usuario_id": "gerente1"}
   ```

**Debe verse:** `diferencia: -1499` (el número exacto puede variar).

**Di:** "El gerente cuenta y no encuentra ninguna. Central le dice cuánto ajustar, pero el ajuste lo hace la tienda, porque la sucursal es la dueña de su inventario físico. Si nadie cuenta en 24 horas, la sospecha se escala al responsable regional."

## Preguntas que puede hacer el profe

**¿Qué pasa si una sucursal pasa horas sin conexión? ¿No parece que dejó de vender todo?**
No. Central solo evalúa una franja cuando ya recibió operaciones de la sucursal posteriores al fin de esa franja. Si la sucursal estuvo desconectada, la franja se evalúa cuando regresa.

**¿Y si se vende algo que el sistema dice que no hay?**
Es lo contrario del fantasma: *inventario negativo*. La caja lo permite (si está en el mostrador, se vende), la venta queda marcada y a la tercera en 24 horas en la misma caja sale una alerta.

**¿Por qué Poisson?**
Cuenta cuántas veces pasa algo en un periodo cuando cada cliente llega de forma independiente, que es como llegan las ventas de un producto en una franja. Da una probabilidad que se puede explicar: "esto pasa por azar menos de 1 de cada 100 veces".

**¿Qué pasa si se cancela una venta?**
La cancelación es un evento nuevo; la venta original no se borra ni se modifica. Por eso los números de venta saltan: el consecutivo cuenta todas las operaciones de la caja (recepciones, ventas, cancelaciones), no solo ventas.

**¿Qué pasa si dos procesos de central aplican la cola al mismo tiempo?**
Se forman en fila con un candado de PostgreSQL. Lo encontramos al preparar esta demo: antes se bloqueaban entre sí y apartaban ventas válidas como fallidas (decisión 0009).

## Si algo falla en vivo

| Síntoma | Qué hacer |
|---|---|
| La pantalla dice **Sin servicio local** | La tienda (terminal 2) está apagada. Levántala. |
| No regresa a **En línea** después de levantar central | Espera 15 s. Si sigue igual, revisa que la terminal 1 diga `Application startup complete`. |
| `simular_fantasma.py` dice que central no tiene los productos | Corre antes `python scripts/cargar_datos_demo.py`. |
| `simular_fantasma.py` dice que central ya tiene otra historia | Usa otra sucursal: `python scripts/simular_fantasma.py --sucursal DEMO2`. |
| `docker compose up -d` marca error de conexión | Docker Desktop no está abierto. |

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { ANA, LECHE, simularApi } from "./apiSimulada";

const ROSA = { usuario_id: "gerente1", nombre: "Rosa Díaz", rol: "gerente" };

type Rutas = Parameters<typeof simularApi>[0];

async function entrarComo(persona: typeof ROSA, rutas: Rutas = {}) {
  const llamadas = simularApi({
    "POST /auth/login": { cuerpo: { ...persona, token: "tok", expira_en: "2026-11-28T06:00:00Z" } },
    [`GET /catalogo/${LECHE.sku}`]: { cuerpo: LECHE },
    ...rutas,
  });
  const usuario = userEvent.setup();
  render(<App />);
  await usuario.type(await screen.findByLabelText("Usuario"), persona.usuario_id);
  await usuario.type(screen.getByLabelText("PIN"), "9999");
  await usuario.click(screen.getByRole("button", { name: "Entrar" }));
  await screen.findByText(persona.nombre);
  return { usuario, llamadas };
}

async function abrirInventario(rutas: Rutas = {}) {
  const sesion = await entrarComo(ROSA, rutas);
  await sesion.usuario.click(screen.getByRole("button", { name: "Inventario" }));
  return sesion;
}

const EVENTO = (consecutivo: number, tipo: string) => ({
  estado: 201,
  cuerpo: { sucursal_id: "S001", caja_id: "C01", consecutivo, tipo, hash: "h" },
});

describe("pestaña Inventario", () => {
  it("la ve el gerente, no el cajero", async () => {
    await entrarComo(ANA);

    expect(screen.queryByRole("button", { name: "Inventario" })).not.toBeInTheDocument();
  });

  it("el gerente la ve y empieza en Recibir mercancía", async () => {
    await abrirInventario();

    expect(screen.getByRole("heading", { name: "Recibir mercancía" })).toBeInTheDocument();
  });
});

describe("recibir mercancía", () => {
  it("captura la factura y envía piezas y costos en centavos", async () => {
    const { usuario, llamadas } = await abrirInventario({
      "POST /inventario/recepciones": EVENTO(9, "recepcion_proveedor"),
    });
    await usuario.type(screen.getByLabelText("Proveedor"), "PROV-LALA");
    await usuario.type(screen.getByLabelText("Folio de la factura"), "A-1520");
    await usuario.type(screen.getByLabelText(/Agregar producto/), `${LECHE.sku}{Enter}`);
    await usuario.type(await screen.findByLabelText("Piezas de Leche entera 1 L"), "12");
    await usuario.type(screen.getByLabelText("Costo unitario de Leche entera 1 L"), "22.50");

    expect(screen.getByText("$270.00")).toBeInTheDocument();
    await usuario.click(screen.getByRole("button", { name: "Registrar recepción" }));

    expect(await screen.findByText(/Recepción registrada \(operación 9\)/)).toBeInTheDocument();
    expect(llamadas.find((l) => l.ruta === "/inventario/recepciones")?.cuerpo).toEqual({
      proveedor_id: "PROV-LALA",
      folio_documento: "A-1520",
      partidas: [{ sku: LECHE.sku, cantidad: 12, costo_unitario_centavos: 2250 }],
    });
  });

  it("un proveedor con espacios se explica antes de enviar", async () => {
    const { usuario, llamadas } = await abrirInventario();
    await usuario.type(screen.getByLabelText("Proveedor"), "Lala SA");
    await usuario.type(screen.getByLabelText("Folio de la factura"), "A-1");

    await usuario.click(screen.getByRole("button", { name: "Registrar recepción" }));

    expect(screen.getByRole("alert")).toHaveTextContent("sin espacios");
    expect(llamadas.some((l) => l.ruta === "/inventario/recepciones")).toBe(false);
  });
});

describe("merma", () => {
  it("registra piezas y motivo, y dice cuántas quedan", async () => {
    let existencia = LECHE.existencia;
    const { usuario, llamadas } = await abrirInventario({
      [`GET /catalogo/${LECHE.sku}`]: () => ({ cuerpo: { ...LECHE, existencia } }),
      "POST /inventario/mermas": () => {
        existencia -= 2;
        return EVENTO(10, "merma");
      },
    });
    await usuario.click(screen.getByRole("button", { name: /Merma/ }));
    await usuario.type(screen.getByLabelText(/Producto/), `${LECHE.sku}{Enter}`);
    await usuario.type(await screen.findByLabelText("Piezas que salen"), "2");
    await usuario.click(screen.getByLabelText("Dañado o roto"));

    await usuario.click(screen.getByRole("button", { name: "Registrar merma" }));

    expect(await screen.findByText(/Quedan 10 en tienda\./)).toBeInTheDocument();
    expect(llamadas.find((l) => l.ruta === "/inventario/mermas")?.cuerpo).toEqual({
      sku: LECHE.sku,
      cantidad: 2,
      motivo: "Dañado o roto",
    });
  });
});

describe("ajuste", () => {
  async function capturarConteo(rutas: Rutas, contadas: string) {
    const sesion = await abrirInventario(rutas);
    await sesion.usuario.click(screen.getByRole("button", { name: /Ajuste/ }));
    await sesion.usuario.type(screen.getByLabelText(/Producto contado/), `${LECHE.sku}{Enter}`);
    await sesion.usuario.type(await screen.findByLabelText("Piezas contadas"), contadas);
    return sesion;
  }

  it("calcula la diferencia y la envía con la aprobación de otra persona", async () => {
    const { usuario, llamadas } = await capturarConteo(
      { "POST /inventario/ajustes": EVENTO(11, "ajuste") },
      "9",
    );
    expect(screen.getByText("−3")).toBeInTheDocument();

    await usuario.click(screen.getByRole("button", { name: "Registrar ajuste" }));
    const dialogo = screen.getByRole("dialog", { name: "Ajustar Leche entera 1 L" });
    await usuario.type(within(dialogo).getByLabelText("Usuario de quien autoriza"), "supervisor1");
    await usuario.type(within(dialogo).getByLabelText("Su PIN"), "4321");
    await usuario.click(within(dialogo).getByRole("button", { name: "Ajustar −3" }));

    expect(await screen.findByText(/Ahora hay 9 en tienda\./)).toBeInTheDocument();
    expect(llamadas.find((l) => l.ruta === "/inventario/ajustes")?.cuerpo).toEqual({
      sku: LECHE.sku,
      diferencia_cantidad: -3,
      motivo: "Conteo físico",
      aprobador_id: "supervisor1",
      aprobador_pin: "4321",
    });
  });

  it("no deja que quien registra se autorice a sí mismo", async () => {
    const { usuario, llamadas } = await capturarConteo({}, "9");
    await usuario.click(screen.getByRole("button", { name: "Registrar ajuste" }));
    const dialogo = screen.getByRole("dialog");
    await usuario.type(within(dialogo).getByLabelText("Usuario de quien autoriza"), "gerente1");
    await usuario.type(within(dialogo).getByLabelText("Su PIN"), "9999");

    await usuario.click(within(dialogo).getByRole("button", { name: "Ajustar −3" }));

    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("Debe autorizar otra persona");
    expect(llamadas.some((l) => l.ruta === "/inventario/ajustes")).toBe(false);
  });

  it("si la tienda rechaza la aprobación, lo explica dentro del diálogo", async () => {
    const { usuario } = await capturarConteo(
      { "POST /inventario/ajustes": { estado: 403, cuerpo: { detail: "Aprobación rechazada: PIN incorrecto." } } },
      "9",
    );
    await usuario.click(screen.getByRole("button", { name: "Registrar ajuste" }));
    const dialogo = screen.getByRole("dialog");
    await usuario.type(within(dialogo).getByLabelText("Usuario de quien autoriza"), "supervisor1");
    await usuario.type(within(dialogo).getByLabelText("Su PIN"), "0000");

    await usuario.click(within(dialogo).getByRole("button", { name: "Ajustar −3" }));

    expect(await within(dialogo).findByRole("alert")).toHaveTextContent("PIN incorrecto");
    expect(within(dialogo).getByLabelText("Su PIN")).toHaveValue("");
  });

  it("si lo contado coincide, no hay nada que ajustar", async () => {
    const { usuario } = await capturarConteo({}, "12");

    await usuario.click(screen.getByRole("button", { name: "Registrar ajuste" }));

    expect(screen.getByRole("alert")).toHaveTextContent("coincide con el sistema");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("inventario fantasma", () => {
  const SOSPECHA = {
    id: 7,
    sku: LECHE.sku,
    nombre: LECHE.nombre,
    estado: "pendiente_conteo",
    franja_inicio: "2026-11-26T18:00:00Z",
    franja_fin: "2026-11-26T22:00:00Z",
    unidades_esperadas: 5.6,
    unidades_vendidas: 0,
    venta_perdida_estimada_centavos: 37285,
    detectada_en: "2026-11-27T02:30:00Z",
    escalada_en: null,
    existencia_local: 12,
  };
  const REPORTE = { cuerpo: { sospechas: [SOSPECHA], venta_perdida_estimada_centavos: 37285 } };

  it("muestra qué se dejó de vender y cuánto se está perdiendo", async () => {
    const { usuario } = await abrirInventario({ "GET /inventario/fantasma": REPORTE });

    await usuario.click(screen.getByRole("button", { name: /Inventario fantasma/ }));

    const tarjeta = await screen.findByRole("article", { name: "Leche entera 1 L" });
    expect(tarjeta).toHaveTextContent("Se esperaban unas 5.6 piezas entre 12:00 y 16:00");
    expect(tarjeta).toHaveTextContent("se vendieron 0");
    expect(screen.getAllByText("$372.85").length).toBeGreaterThan(0);
  });

  it("el conteo cierra la sospecha y lleva al ajuste con lo contado", async () => {
    const { usuario, llamadas } = await abrirInventario({
      "GET /inventario/fantasma": REPORTE,
      "POST /inventario/fantasma/7/conteo": {
        cuerpo: { sospecha_id: 7, sku: LECHE.sku, cantidad_contada: 0, existencia_local: 12, diferencia: -12 },
      },
    });
    await usuario.click(screen.getByRole("button", { name: /Inventario fantasma/ }));
    const tarjeta = await screen.findByRole("article", { name: "Leche entera 1 L" });
    await usuario.type(within(tarjeta).getByLabelText(/Piezas contadas/), "0");

    await usuario.click(within(tarjeta).getByRole("button", { name: "Registrar conteo" }));
    await usuario.click(await screen.findByRole("button", { name: "Registrar ajuste de −12" }));

    expect(llamadas.find((l) => l.ruta === "/inventario/fantasma/7/conteo")?.cuerpo).toEqual({
      cantidad_contada: 0,
    });
    expect(await screen.findByRole("heading", { name: "Ajustar inventario" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Piezas contadas")).toHaveValue("0");
    expect(screen.getByText("−12")).toBeInTheDocument();
  });

  it("sin conexión con central lo explica", async () => {
    const { usuario } = await abrirInventario({
      "GET /inventario/fantasma": {
        estado: 503,
        cuerpo: { detail: "Sin conexión con central. El reporte de inventario fantasma vive en central." },
      },
    });

    await usuario.click(screen.getByRole("button", { name: /Inventario fantasma/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Sin conexión con central");
  });
});

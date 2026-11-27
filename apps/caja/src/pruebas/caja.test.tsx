import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { guardarToken, leerToken } from "../sesion";
import { ANA, LECHE, REFRESCO, simularApi } from "./apiSimulada";

const LOGIN_OK = { cuerpo: { ...ANA, token: "tok-ana", expira_en: "2026-11-28T06:00:00Z" } };

async function abrirCaja(rutas: Parameters<typeof simularApi>[0] = {}) {
  const llamadas = simularApi({ "POST /auth/login": LOGIN_OK, ...rutas });
  const usuario = userEvent.setup();
  render(<App />);
  await usuario.type(await screen.findByLabelText("Usuario"), "cajero1");
  await usuario.type(screen.getByLabelText("PIN"), "1234");
  await usuario.click(screen.getByRole("button", { name: "Entrar" }));
  await screen.findByText("Ana López");
  return { usuario, llamadas };
}

async function escanear(usuario: ReturnType<typeof userEvent.setup>, codigo: string) {
  const escaner = screen.getByLabelText(/Código del producto/);
  await usuario.type(escaner, `${codigo}{Enter}`);
}

const productos: Record<string, { cuerpo: unknown }> = {
  [`GET /catalogo/${REFRESCO.sku}`]: { cuerpo: REFRESCO },
  [`GET /catalogo/${LECHE.sku}`]: { cuerpo: LECHE },
};

describe("inicio de sesión", () => {
  it("con usuario y PIN correctos abre la caja y guarda la sesión", async () => {
    await abrirCaja();

    expect(screen.getByText("Caja C01 de la sucursal S001")).toBeInTheDocument();
    expect(leerToken()).toBe("tok-ana");
  });

  it("con PIN incorrecto explica el error y borra el PIN", async () => {
    simularApi({ "POST /auth/login": { estado: 401, cuerpo: { detail: "Usuario o PIN incorrectos." } } });
    const usuario = userEvent.setup();
    render(<App />);
    await usuario.type(await screen.findByLabelText("Usuario"), "cajero1");
    await usuario.type(screen.getByLabelText("PIN"), "0000");
    await usuario.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Usuario o PIN incorrectos.");
    expect(screen.getByLabelText("PIN")).toHaveValue("");
  });

  it("al recargar la pantalla reanuda la sesión guardada sin pedir PIN", async () => {
    guardarToken("tok-guardado");
    const llamadas = simularApi({ "GET /auth/yo": { cuerpo: ANA } });
    render(<App />);

    expect(await screen.findByText("Ana López")).toBeInTheDocument();
    expect(llamadas.find((l) => l.ruta === "/auth/yo")?.token).toBe("tok-guardado");
  });

  it("si la sesión guardada ya no sirve, pide entrar de nuevo", async () => {
    guardarToken("tok-viejo");
    simularApi({ "GET /auth/yo": { estado: 401, cuerpo: { detail: "La sesión expiró." } } });
    render(<App />);

    expect(await screen.findByRole("button", { name: "Entrar" })).toBeInTheDocument();
    expect(leerToken()).toBeNull();
  });
});

describe("cobro", () => {
  it("escanear agrega al ticket y suma; el mismo producto dos veces aumenta la cantidad", async () => {
    const { usuario } = await abrirCaja(productos);

    await escanear(usuario, REFRESCO.sku);
    await escanear(usuario, REFRESCO.sku);
    await escanear(usuario, LECHE.sku);

    const ticket = screen.getByRole("region", { name: "Ticket de la venta en curso" });
    expect(within(ticket).getByText("Refresco de cola 600 ml")).toBeInTheDocument();
    expect(within(ticket).getByText("2 × $21.00")).toBeInTheDocument();
    expect(screen.getByTestId("total")).toHaveTextContent("$72.00");
    expect(screen.getByRole("button", { name: /Cobrar \$72\.00/ })).toBeEnabled();
  });

  it("un código que no existe se explica sin tocar el ticket", async () => {
    const { usuario } = await abrirCaja({
      "GET /catalogo/999": { estado: 404, cuerpo: { detail: "No existe" } },
    });

    await escanear(usuario, "999");

    expect(await screen.findByRole("alert")).toHaveTextContent("No hay ningún producto con el código 999.");
    expect(screen.getByTestId("total")).toHaveTextContent("$0.00");
  });

  it("un precio vencido muestra el motivo que da la tienda", async () => {
    const { usuario } = await abrirCaja({
      [`GET /catalogo/${REFRESCO.sku}`]: {
        estado: 409,
        cuerpo: { detail: "El precio del SKU '7501055300846' superó el TTL de 72 horas." },
      },
    });

    await escanear(usuario, REFRESCO.sku);

    expect(await screen.findByRole("alert")).toHaveTextContent("superó el TTL de 72 horas");
  });

  it("F12 cobra, envía las cantidades y deja el cambio a la vista", async () => {
    const { usuario, llamadas } = await abrirCaja({
      ...productos,
      "POST /pos/ventas": {
        estado: 201,
        cuerpo: {
          sucursal_id: "S001",
          caja_id: "C01",
          consecutivo: 7,
          total_centavos: 7200,
          hash: "h",
          inventario_negativo: false,
          alerta_inventario_negativo: false,
          skus_costo_por_revisar: [],
        },
      },
    });
    await escanear(usuario, REFRESCO.sku);
    await escanear(usuario, REFRESCO.sku);
    await escanear(usuario, LECHE.sku);
    await usuario.type(screen.getByLabelText("Paga con"), "100");

    await usuario.keyboard("{F12}");

    expect(await screen.findByText("Venta 7 cobrada por $72.00.")).toBeInTheDocument();
    expect(screen.getByText("Cambio: $28.00")).toBeInTheDocument();
    const cobro = llamadas.find((l) => l.metodo === "POST" && l.ruta === "/pos/ventas");
    expect(cobro?.token).toBe("tok-ana");
    expect(cobro?.cuerpo).toEqual({
      items: [
        { sku: REFRESCO.sku, cantidad: 2 },
        { sku: LECHE.sku, cantidad: 1 },
      ],
    });
    expect(screen.getByTestId("total")).toHaveTextContent("$0.00");
  });

  it("no cobra si el pago no alcanza", async () => {
    const { usuario, llamadas } = await abrirCaja(productos);
    await escanear(usuario, LECHE.sku);
    await usuario.type(screen.getByLabelText("Paga con"), "20");

    await usuario.click(screen.getByRole("button", { name: /Cobrar \$/ }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Faltan $10.00 para cubrir el total.");
    expect(llamadas.some((l) => l.ruta === "/pos/ventas")).toBe(false);
  });

  it("avisa al cajero cuando se vendió sin existencias registradas", async () => {
    const { usuario } = await abrirCaja({
      ...productos,
      "POST /pos/ventas": {
        estado: 201,
        cuerpo: {
          sucursal_id: "S001",
          caja_id: "C01",
          consecutivo: 8,
          total_centavos: 3000,
          hash: "h",
          inventario_negativo: true,
          alerta_inventario_negativo: true,
          skus_costo_por_revisar: [],
        },
      },
    });
    await escanear(usuario, LECHE.sku);

    await usuario.keyboard("{F12}");

    expect(await screen.findByText(/tercera venta de hoy sin existencias/)).toBeInTheDocument();
  });

  it("si la sesión vence al cobrar, regresa al inicio de sesión explicando por qué", async () => {
    const { usuario } = await abrirCaja({
      ...productos,
      "POST /pos/ventas": { estado: 403, cuerpo: { detail: "El usuario 'cajero1' está dado de baja." } },
    });
    await escanear(usuario, LECHE.sku);

    await usuario.keyboard("{F12}");

    expect(await screen.findByText(/Se cerró la sesión: El usuario 'cajero1' está dado de baja\./)).toBeInTheDocument();
    expect(leerToken()).toBeNull();
  });

  it("buscar por nombre muestra resultados y elegir uno lo agrega", async () => {
    const { usuario } = await abrirCaja({
      "GET /catalogo": {
        cuerpo: [
          { ...REFRESCO, precio_vigente: true },
          { ...LECHE, sku: "vencido", nombre: "Refresco viejo", precio_vigente: false },
        ],
      },
    });

    await usuario.type(screen.getByLabelText("Buscar por nombre"), "refresco");
    const opcion = await screen.findByRole("button", { name: /Refresco de cola 600 ml/ });
    expect(screen.getByRole("button", { name: /Refresco viejo/ })).toBeDisabled();
    await usuario.click(opcion);

    expect(screen.getByTestId("total")).toHaveTextContent("$21.00");
    expect(screen.getByLabelText("Buscar por nombre")).toHaveValue("");
  });
});

describe("ventas del turno", () => {
  const VENTA = {
    consecutivo: 5,
    registrado_en: "2026-11-27T22:10:00.000+00:00",
    usuario_id: "cajero1",
    total_centavos: 4200,
    inventario_negativo: false,
    cancelada: false,
    consecutivo_cancelacion: null,
    hash: "h",
    partidas: [
      {
        sku: REFRESCO.sku,
        nombre: REFRESCO.nombre,
        cantidad: 2,
        precio_unitario_centavos: 2100,
        subtotal_centavos: 4200,
      },
    ],
  };

  it("cancelar pide gerente y PIN, y envía la aprobación", async () => {
    let cancelada = false;
    const { usuario, llamadas } = await abrirCaja({
      "GET /pos/ventas": () => ({ cuerpo: [{ ...VENTA, cancelada, consecutivo_cancelacion: cancelada ? 6 : null }] }),
      "POST /pos/ventas/5/cancelar": () => {
        cancelada = true;
        return { estado: 201, cuerpo: { consecutivo: 6 } };
      },
    });
    await usuario.click(screen.getByRole("button", { name: "Ventas del turno" }));
    await usuario.click(await screen.findByRole("button", { name: "Cancelar" }));

    const dialogo = screen.getByRole("dialog", { name: "Cancelar la venta 5" });
    await usuario.type(within(dialogo).getByLabelText("Usuario del gerente"), "gerente1");
    await usuario.type(within(dialogo).getByLabelText("PIN del gerente"), "9999");
    await usuario.click(within(dialogo).getByRole("button", { name: "Cancelar venta" }));

    expect(await screen.findByText("Venta 5 cancelada.")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Cancelada")).toBeInTheDocument();
    const peticion = llamadas.find((l) => l.ruta === "/pos/ventas/5/cancelar");
    expect(peticion?.cuerpo).toEqual({
      motivo: "Devolución del cliente",
      aprobador_id: "gerente1",
      aprobador_pin: "9999",
    });
  });

  it("si la aprobación se rechaza, lo explica dentro del diálogo", async () => {
    const { usuario } = await abrirCaja({
      "GET /pos/ventas": { cuerpo: [VENTA] },
      "POST /pos/ventas/5/cancelar": {
        estado: 403,
        cuerpo: { detail: "Nadie puede aprobar su propia operación." },
      },
    });
    await usuario.click(screen.getByRole("button", { name: "Ventas del turno" }));
    await usuario.click(await screen.findByRole("button", { name: "Cancelar" }));
    const dialogo = screen.getByRole("dialog");
    await usuario.type(within(dialogo).getByLabelText("Usuario del gerente"), "cajero1");
    await usuario.type(within(dialogo).getByLabelText("PIN del gerente"), "1234");

    await usuario.click(within(dialogo).getByRole("button", { name: "Cancelar venta" }));

    expect(await within(dialogo).findByRole("alert")).toHaveTextContent(
      "Nadie puede aprobar su propia operación.",
    );
    expect(within(dialogo).getByLabelText("PIN del gerente")).toHaveValue("");
  });

  it("Escape cierra el diálogo sin cancelar", async () => {
    const { usuario, llamadas } = await abrirCaja({ "GET /pos/ventas": { cuerpo: [VENTA] } });
    await usuario.click(screen.getByRole("button", { name: "Ventas del turno" }));
    await usuario.click(await screen.findByRole("button", { name: "Cancelar" }));

    await usuario.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(llamadas.some((l) => l.ruta.endsWith("/cancelar"))).toBe(false);
  });
});

describe("cerrar sesión", () => {
  it("avisa a la tienda, borra la sesión y regresa al inicio", async () => {
    const { usuario, llamadas } = await abrirCaja({ "POST /auth/logout": { estado: 204 } });

    await usuario.click(screen.getByRole("button", { name: "Cerrar sesión" }));

    expect(await screen.findByRole("button", { name: "Entrar" })).toBeInTheDocument();
    expect(leerToken()).toBeNull();
    expect(llamadas.find((l) => l.ruta === "/auth/logout")?.token).toBe("tok-ana");
  });
});

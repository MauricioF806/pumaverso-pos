import { describe, expect, it } from "vitest";
import { resumirEstado } from "../componentes/EstadoConexion";
import type { EstadoSincronizacion } from "../tipos";
import { ESTADO_EN_LINEA } from "./apiSimulada";

const con = (cambios: Partial<EstadoSincronizacion["trabajador"]>, pendientes = 0) => ({
  ...ESTADO_EN_LINEA,
  pendientes,
  trabajador: { ...ESTADO_EN_LINEA.trabajador!, ...cambios },
});

describe("resumirEstado", () => {
  it("en línea no muestra pendientes: se envían solos", () => {
    expect(resumirEstado(con({}, 3), false)).toEqual({ tono: "bien", titulo: "En línea" });
  });

  it("sin conexión con central indica cuántas operaciones faltan por enviar", () => {
    expect(resumirEstado(con({ fallos_consecutivos: 2 }, 3), false)).toEqual({
      tono: "espera",
      titulo: "Sin conexión con central",
      detalle: "3 operaciones por enviar",
    });
    expect(resumirEstado(con({ fallos_consecutivos: 1 }, 1), false).detalle).toBe(
      "1 operación por enviar",
    );
  });

  it("un conflicto pide avisar al gerente", () => {
    expect(resumirEstado(con({ en_conflicto: true }), false)).toMatchObject({
      tono: "problema",
      detalle: "Avisa al gerente",
    });
  });

  it("si el servicio local no responde, no se puede cobrar", () => {
    expect(resumirEstado(null, true)).toMatchObject({ tono: "problema", titulo: "Sin servicio local" });
  });

  it("antes de la primera sincronización dice que está conectando", () => {
    expect(resumirEstado(con({ ultimo_exito_en: null }), false).titulo).toBe("Conectando con central");
  });
});

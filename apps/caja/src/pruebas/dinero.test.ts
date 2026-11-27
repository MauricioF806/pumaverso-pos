import { describe, expect, it } from "vitest";
import { pesos } from "../dinero";

describe("pesos", () => {
  it("convierte centavos a pesos mexicanos", () => {
    expect(pesos(2150)).toBe("$21.50");
    expect(pesos(0)).toBe("$0.00");
    expect(pesos(123456)).toBe("$1,234.56");
  });
});

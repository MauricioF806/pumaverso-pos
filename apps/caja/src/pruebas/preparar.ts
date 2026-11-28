import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, expect, vi } from "vitest";

// React avisa por consola cuando el HTML queda mal anidado (por ejemplo, un
// <form> dentro de otro). jsdom lo tolera, pero en el navegador real un Enter
// recarga la página. Esas advertencias hacen fallar la prueba.
const errorOriginal = console.error;
let malAnidado: string[] = [];

beforeEach(() => {
  malAnidado = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    const texto = args.map(String).join(" ");
    if (/cannot contain a nested|cannot be a (child|descendant) of|validateDOMNesting/.test(texto)) {
      malAnidado.push(texto);
    }
    errorOriginal(...args);
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.localStorage.clear();
  expect(malAnidado, "HTML mal anidado").toEqual([]);
});

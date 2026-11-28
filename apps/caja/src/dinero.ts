const formato = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });

/** 2150 → "$21.50". Los importes viajan siempre en centavos (enteros). */
export function pesos(centavos: number): string {
  return formato.format(centavos / 100);
}

/** "21.5" o "21,50" → 2150. Devuelve null si no es un importe válido. */
export function aCentavos(texto: string): number | null {
  const limpio = texto.trim().replace(",", ".").replace(/^\$/, "");
  if (!/^\d+(\.\d{0,2})?$/.test(limpio)) return null;
  return Math.round(Number.parseFloat(limpio) * 100);
}

/** -21 → "−21"; 3 → "+3". Para diferencias de inventario. */
export function conSigno(n: number): string {
  if (n > 0) return `+${n}`;
  if (n < 0) return `−${Math.abs(n)}`;
  return "0";
}

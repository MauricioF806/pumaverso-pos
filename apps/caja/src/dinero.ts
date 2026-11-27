const formato = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN" });

/** 2150 → "$21.50". Los importes viajan siempre en centavos (enteros). */
export function pesos(centavos: number): string {
  return formato.format(centavos / 100);
}

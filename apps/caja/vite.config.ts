import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  server: {
    // Debe coincidir con CORS_ORIGENES de la tienda (decisión 0008).
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/pruebas/preparar.ts"],
    // Las horas se muestran en la zona de la sucursal; así las pruebas no dependen de la compu.
    env: { TZ: "America/Mexico_City" },
  },
});

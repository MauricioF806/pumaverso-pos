# 0002. El código de central vive en `apps/central/`

- **Estado:** Aceptada
- **Fecha:** 2026-11-21
- **Decidido por:** Equipo Pumaverso Distribuido

## Contexto

Hasta ahora el repositorio solo contiene el monolito de la sucursal en `apps/tienda/`. Los servicios centrales (Sincronización, Usuarios, Catálogo y precios, Inventario) necesitan un lugar en el repositorio.

## Decisión

El código de los servicios centrales vive en **`apps/central/`**, al mismo nivel que `apps/tienda/` y en el mismo repositorio.

- Sigue el mismo patrón que la tienda: proyecto de Python con su propio `pyproject.toml`, código en `src/` y pruebas en `tests/`.
- Cada servicio central tiene su propio esquema en PostgreSQL, como indica el contexto del proyecto.
- La estructura interna (un paquete por servicio, cómo se despliegan) se define al iniciar el desarrollo de los servicios centrales.

## Consecuencias

**A favor**

- Un solo repositorio: un cambio que toca tienda y central puede revisarse en un mismo Pull Request.
- La estructura es predecible para todo el equipo.

**En contra**

- La tienda y central comparten historial y reglas de integración; un error en una parte puede bloquear la integración de la otra.

## Alternativas consideradas

- **Un repositorio separado para central:** aísla mejor, pero obliga a coordinar versiones entre dos repositorios. Es demasiado costo para un equipo de cuatro personas.

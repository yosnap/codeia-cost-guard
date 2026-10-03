---
title: "costbar v0.2.0: hora local y panel movil"
date: 2026-10-03
summary: "Fix de timestamps UTC en costbar (ritmo/ventana/dias en hora local) y version movil del panel; lanzado como v0.2.0 con issues #1 y #2 cerrados."
---

# costbar v0.2.0: hora local y panel movil

## What happened

Sesion cerrada del repo codeia-cost-guard (barra de menu + panel de consumo de tokens):

1. **fix(panel) a4dd00f** — issue #1: los logs de Claude Code y Codex guardan `timestamp`
   en UTC con sufijo `Z`; `parsear()` y `mete()` de costbar.py troceaban el texto tal cual
   y `agregar()` interpretaba la clave `YYYY-MM-DDTHH` como hora local. Resultado en zonas
   != UTC: "ritmo ultima hora" siempre 0, ventana de 5 h corta y el corte dia/ayer a
   las 00-01 locales en vez de medianoche. Arreglo: nueva funcion `a_local()` (solo
   convierte si hay zona, `Z` o `±HH:MM`) aplicada en `parsear()` y en `mete()` (dentro
   de `leer_bases()`). `parsear_pi` ya lo hacia inline, sin tocar. `VERSION_ESTADO`
   sube 9 → 10 para reescanear el caché (claves antiguas en UTC).
   - Verificado: casos de `a_local()` (verano 22:45, invierno 13:00, offset, cadena
     rota), dedup de doble registro de Codex intacta, `--print` en vivo: ritmo 1,9 M
     (antes 0).
2. **feat(panel) 5ba2d1a** — issue #2: version movil del panel. `<meta name="viewport">`
   en el head que genera `construye()` (panel.py) + bloque media <=600px al final de
   panel_assets/estilos.css: KPI en 2 columnas, filas compactas, tablas anchas con scroll
   horizontal dentro de su tarjeta, calendario a todo el ancho. Sin cambiar colores,
   tipografias ni componentes. Ademas en escritorio: `.zona>div{min-width:0}` (el bloque
   "Por dia" ya no ensancha su caja) y `#detalle-dia:empty{display:none}`.
   - Verificado con Chromium real a 390px (sin overflow, rejilla 2 cols) y a 1280px
     (sin regresiones, rejilla 5 cols, detalle vacio oculto).

## Decisions

- Camino "ligero" de versionado: commits convencionales directos en `main` + tag +
  GitHub release, sin instalar el flujo completo feature→develop→main de la skill
  branching-avanzado (solo un commiteador; sobria ceremonia).
- Release v0.2.0 agrupa fix (patch) + feat (minor): el minor absorbe al patch.
- Eliminada la rama local huérfana `Cost-Guard` (ya completamente mergeada en main,
  verificada con `git branch --merged` antes del `git branch -d`).
- Sin suite de tests formal todavía: la verificacion fue ad-hoc (script de casos +
  render real del panel).

## Next steps

- Opcional: tests/ con pytest para `a_local()` y `parsear()` (dedup Codex, horas locales)
  para verificar con un solo comando antes de cada push.
- Proxima version: v0.2.1 (fix) o v0.3.0 (feat), mismo patron: commits convencionales
  → push → tag + release.

AgentWiki publish skipped.

> Historical work record — not durable authority. Prefer docs/specs/ADRs for current decisions.

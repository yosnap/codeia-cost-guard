# Contribuir a codeia-cost-guard

Gracias por el interés. Esto es una herramienta pequeña y de un solo propósito: medir el gasto de
tokens de tus agentes. Las contribuciones más útiles son correcciones concretas, no reescrituras.

## Antes de empezar

1. Comprueba si ya hay una [issue](https://github.com/yosnap/codeia-cost-guard/issues) abierta
   sobre lo mismo. Si no la hay y el cambio es más que trivial, abre una describiendo el problema
   o la propuesta antes de ponerte a programar: evita trabajo duplicado o que un PR grande se
   rechace por no encajar con el enfoque del proyecto.
2. Para bugs, incluye: sistema operativo, versión de Python (`python3 --version`) y la salida
   completa del error.

## Cómo preparar el entorno

```bash
git clone https://github.com/yosnap/codeia-cost-guard.git
cd codeia-cost-guard

# El motor (costbar.py, analizar_gasto.py, panel.py, precio_astra.py) solo necesita Python 3.9+,
# funciona igual en macOS, Linux y Windows.
python3 analizar_gasto.py
python3 panel.py

# Solo en macOS, si tocas la barra de menus (barra.py / popover.py):
python3 -m venv venv
venv/bin/pip install pillow pyobjc-framework-Cocoa
venv/bin/python barra.py --abre
```

No hay suite de tests automatizada todavía. La verificación manual mínima antes de un PR:

```bash
python3 analizar_gasto.py          # no debe lanzar excepciones
python3 panel.py && open panel.html   # (o xdg-open / start en Linux/Windows)
```

Si tu cambio toca `costbar.py`, prueba también con un `$HOME` vacío para asegurarte de que no
rompe la primera ejecución (sin logs de Claude Code, Codex u OpenCode todavía):

```bash
HOME=/tmp/prueba-limpia python3 analizar_gasto.py
```

## Qué encaja en este repo

- Arreglos de conteo de tokens (formatos de log de Claude Code / Codex / OpenCode, turnos
  duplicados, tarifas de `TARIFAS` en `costbar.py` cuando salga un modelo nuevo).
- Mejoras de compatibilidad multiplataforma del motor (Windows/Linux), siempre sin romper macOS.
- Correcciones al panel (`panel.py`, `panel_assets/`) o a la barra de menús (macOS).
- Documentación (`README.md`, `GUIA.md`) cuando el cambio de código lo deje desactualizado.

Lo que probablemente no encaje: dependencias nuevas pesadas, un backend/servidor remoto, o
soporte para agentes que no dejan log local (eso lo dice el propio README: hay que ejecutar el
medidor en la máquina donde corre el agente).

## Estilo de commits

El repo usa mensajes cortos en español, con el formato `tipo(ámbito): qué cambia y por qué`
cuando el ámbito ayuda a entenderlo (`fix(costbar): ...`, `docs(readme): ...`, `feat(panel): ...`).
Mira `git log --oneline` para el tono. Un commit, un cambio coherente; evita mezclar un fix con un
refactor sin relación.

## Pull requests

1. Rama descriptiva a partir de `main`.
2. Un PR por cambio lógico. Explica en la descripción qué comportamiento cambia y cómo lo has
   probado (sistema operativo incluido).
3. Si el cambio afecta a lo que ve el usuario (README, instalación, capturas), actualiza también
   la documentación en el mismo PR.

## Licencia

Al contribuir aceptas que tu aportación se publique bajo la licencia MIT del repo (ver
[LICENSE](LICENSE)).

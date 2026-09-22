# codeia-cost-guard

Mide (y ayuda a frenar) el gasto de tokens de tu flota de agentes: **Claude Code, Codex, OpenCode**.
Lee sus logs locales, descarta los turnos duplicados por los *forks*, y te lo enseña en un panel y en
un icono de la barra de menús.

Hecho por [Codeia](https://codeia.dev) — el sitio donde se aprenden estas tecnologías.

## Por qué

No te arruina el *effort* del modelo: te arruina **cuántas veces le mandas el contexto**. El 75 % del
gasto típico es lectura de caché — o sea, el mismo contexto reenviado turno tras turno. Y por encima de
**272.000 tokens** toda la petición pasa a facturarse a 2× entrada / 1,5× salida. Con esto delante, un
día de trabajo se ve de un vistazo. El detalle completo, en [GUIA.md](GUIA.md).

## Instalación

```bash
git clone https://github.com/yosnap/codeia-cost-guard.git
cd codeia-cost-guard

# macOS: instala todo (app de barra + panel + reglas opcionales)
./install.sh

# Cualquier sistema: solo el medidor y el panel (necesita únicamente Python 3.9+)
python3 analizar_gasto.py      # informe en el terminal
python3 panel.py               # genera panel.html y lo abres en el navegador
```

En macOS, `install.sh` deja la app arrancando sola al iniciar sesión. Para pegar además las reglas de
ahorro en `~/.codex/AGENTS.md` y `~/.claude/CLAUDE.md`: `./install.sh --reglas`.

## Qué trae

- **`costbar.py`** — la app de barra de menús (macOS). Muestra el gasto de hoy y, al clicarla, el ritmo
  de la última hora, ayer, 7 días y el desglose por modelo y por proyecto. Avisa por notificación si el
  ritmo pasa de un límite (`config.json`).
- **`panel.py`** — el panel visual: `panel.html` con barras por día, modelo y proyecto. Se refresca
  solo cada minuto.
- **`analizar_gasto.py`** — el informe de texto, para terminal o para cron.
- **`precio_astra.py`** — calculadora de precios con *preflight*: estima el coste de una petición antes
  de enviarla (incluido el precipicio de los 272K, caché, Batch y Fast).
- **`instalar_reglas_globales.py`** — escribe las reglas de ahorro en los ficheros de instrucciones
  globales de los agentes CLI. Idempotente y con copia de seguridad.
- **`GUIA.md`** — la guía: las cinco palancas y por qué el contexto manda.

## Cómo cuenta

Lee los logs de sesión de `~/.claude`, `~/.codex` y `~/.opencode`, se queda con los turnos **únicos**
(los forks de Claude Code copian los turnos de sus padres y contarían doble) y aplica los precios de
lista de cada modelo.

**Los dólares son equivalente API**, no tu factura: si vas con suscripción no pagas por token, pero lo
que se agota es lo mismo que aquí se mide — *tokens × turnos*. Los agentes que corren en la nube no
dejan log en tu máquina: ejecuta el medidor **en esas máquinas** y suma los totales.

## Licencia

MIT.

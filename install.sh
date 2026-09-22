#!/usr/bin/env bash
# Instala codeia-cost-guard.
#   macOS  -> barra de menus con estilo propio (icono + desplegable) y panel
#   Otros  -> medidor y panel (solo necesita Python 3.9+)
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${COSTGUARD_HOME:-$HOME/.cost-guard}"
REGLAS=0; [ "${1:-}" = "--reglas" ] && REGLAS=1

echo "-> Copiando a $DESTINO"
mkdir -p "$DESTINO/logs"
for f in costbar.py barra.py popover.py panel.py costbar.css analizar_gasto.py \
         precio_astra.py instalar_reglas_globales.py GUIA.md AGENTS-snippet.md; do
  [ -f "$AQUI/$f" ] && cp "$AQUI/$f" "$DESTINO/$f"
done
# config.json no se pisa: si ya existe trae tus cuotas y notas por proveedor (como state.json)
[ -f "$DESTINO/config.json" ] || cp "$AQUI/config.json" "$DESTINO/config.json"
[ -f "$DESTINO/state.json" ] || echo "{}" > "$DESTINO/state.json"

if [ "$(uname -s)" = "Darwin" ]; then
  echo "-> Python + pillow + pyobjc"
  python3 -m venv "$DESTINO/venv" 2>/dev/null || true
  "$DESTINO/venv/bin/pip" install --quiet --upgrade pip
  "$DESTINO/venv/bin/pip" install --quiet pillow pyobjc-framework-Cocoa

  if [ ! -e "/Applications/Google Chrome.app" ] && ! command -v chromium >/dev/null 2>&1; then
    echo "  aviso: sin Chrome/Chromium el desplegable no se pinta (el icono si funciona)."
  fi

  echo "-> Arranque automatico al iniciar sesion"
  PLIST="$HOME/Library/LaunchAgents/dev.codeia.costguard.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.codeia.costguard</string>
  <key>ProgramArguments</key><array>
    <string>$DESTINO/venv/bin/python</string><string>$DESTINO/barra.py</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DESTINO/logs/barra.log</string>
  <key>StandardErrorPath</key><string>$DESTINO/logs/barra.err</string>
</dict></plist>
PLIST
  launchctl bootout "gui/$(id -u)/dev.codeia.costguard" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null || launchctl load "$PLIST" 2>/dev/null || true
  echo ""
  echo "Listo: mira la barra de menus (icono con la cifra de las ultimas 5 h)."
  echo "  Log:     $DESTINO/logs/barra.log"
  echo "  Parar:   launchctl bootout gui/$(id -u)/dev.codeia.costguard"
  echo "  Arrancar launchctl bootstrap gui/$(id -u) $PLIST"
  echo "  Prueba:  $DESTINO/venv/bin/python $DESTINO/barra.py --abre"
else
  echo "Listo (sin barra de menus en este sistema)."
fi

if [ "$REGLAS" = 1 ] && [ -f "$DESTINO/instalar_reglas_globales.py" ]; then
  python3 "$DESTINO/instalar_reglas_globales.py" || true
fi
echo ""
echo "Panel: python3 \"$DESTINO/panel.py\"  (genera panel.html)"

#!/usr/bin/env bash
# Instala codeia-cost-guard. macOS: app de barra de menus + panel. Linux/Windows: panel y medidor.
set -euo pipefail
AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="$HOME/.cost-guard"
REGLAS=0; [ "${1:-}" = "--reglas" ] && REGLAS=1

echo "→ Copiando a $DESTINO"
mkdir -p "$DESTINO/logs"
for f in costbar.py panel.py analizar_gasto.py precio_astra.py instalar_reglas_globales.py config.json GUIA.md AGENTS-snippet.md; do
  [ -f "$AQUI/$f" ] && cp "$AQUI/$f" "$DESTINO/$f"
done

if [ "$(uname -s)" = "Darwin" ]; then
  echo "→ Entorno de Python + rumps"
  python3 -m venv "$DESTINO/venv" 2>/dev/null || true
  "$DESTINO/venv/bin/pip" install --quiet --upgrade pip
  "$DESTINO/venv/bin/pip" install --quiet rumps pyobjc-framework-Cocoa

  echo "→ Arranque automático al iniciar sesión"
  PLIST="$HOME/Library/LaunchAgents/dev.codeia.costguard.plist"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.codeia.costguard</string>
  <key>ProgramArguments</key><array>
    <string>$DESTINO/venv/bin/python</string><string>$DESTINO/costbar.py</string>
  </array>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DESTINO/logs/costbar.log</string>
  <key>StandardErrorPath</key><string>$DESTINO/logs/costbar.err</string>
</dict></plist>
PLIST
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST"
  echo "→ Listo: mira la barra de menús (icono con el gasto de hoy)."
else
  echo "→ Linux/Windows: usa el medidor y el panel"
  echo "   python3 $DESTINO/analizar_gasto.py"
  echo "   python3 $DESTINO/panel.py   # abre panel.html"
fi

if [ "$REGLAS" = "1" ]; then
  echo "→ Reglas de ahorro en los ficheros de instrucciones globales"
  python3 "$DESTINO/instalar_reglas_globales.py"
fi
echo "Hecho. Guia: $DESTINO/GUIA.md"

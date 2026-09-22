#!/usr/bin/env python3
"""CostBar: icono en la barra de menus con el desplegable en HTML (estilo propio).

Por que no usa rumps: rumps esta atado al menu nativo de macOS, y el menu nativo no se puede
pintar (ni colores, ni tipografia, ni columnas alineadas). Aqui se construye el icono a mano
con NSStatusItem y el desplegable es un NSPopover con un WKWebView: dentro manda el HTML, asi
que el estilo es libre. Es el mismo enfoque que usan Stats, Raycast o Bartender.

Uso:
    ./venv/bin/python barra.py            # icono vivo, clic = desplegable
    ./venv/bin/python barra.py --abre     # ademas abre el desplegable al arrancar (pruebas)
"""
import os
import re
import subprocess
import sys

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSColor,
    NSImage,
    NSMakeRect,
    NSMakeSize,
    NSPopover,
    NSStatusBar,
    NSVariableStatusItemLength,
    NSImageView,
    NSViewController,
)
from Foundation import NSData, NSObject, NSTimer, NSURL
from PIL import Image, ImageDraw, ImageFont


AQUI = os.path.dirname(os.path.abspath(__file__))


def navegador():
    """El motor que convierte el HTML en imagen. Chrome headless, o Chromium/Brave."""
    for ruta in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium",
                 "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                 "/usr/bin/chromium", "/usr/bin/google-chrome"):
        if os.path.exists(ruta):
            return ruta
    return None
PY = f"{AQUI}/venv/bin/python"
LOG = f"{AQUI}/logs/barra.log"

VIOLETA = (174, 0, 255)
ROSA = (255, 0, 140)
SUAVE = (154, 120, 214)
GRIS = (0, 0, 0, 40)
ANCHO_ICONO = 74


def log(txt):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{subprocess.run(['date', '+%Y-%m-%d %H:%M:%S'], capture_output=True, text=True).stdout.strip()} {txt}\n")


def trocea(txt):
    """'210,4 M' -> (210.4, 'M')."""
    m = re.match(r"\s*([\d.,]+)\s*([KMB]?)", str(txt))
    if not m:
        return (0.0, "")
    n = float(m.group(1).replace(".", "").replace(",", "."))
    return (n, m.group(2) or "")


def color_magnitud(u):
    return {"K": SUAVE, "M": VIOLETA, "B": ROSA}.get(u, VIOLETA)


def datos():
    """Lee el informe del motor y saca lo que necesita el icono."""
    try:
        t = subprocess.run([PY, f"{AQUI}/costbar.py", "--print"], capture_output=True, text=True, timeout=300).stdout
    except Exception as e:
        log(f"ERROR motor {type(e).__name__}")
        return None
    d = {}
    for clave, pat in (("c5", r"VENTANA 5 H\s+([\d.,]+\s*[KMB]?)"),
                       ("hoy", r"HOY\s+([\d.,]+\s*[KMB]?)"),
                       ("turnos", r"HOY\s+[\d.,]+\s*[KMB]? tokens \((\d+)")):
        m = re.search(pat, t)
        d[clave] = m.group(1) if m else ""
    return d


def imagen_icono(texto, color, pct):
    """El icono: barrita de progreso + cifra en el color de su magnitud."""
    escala = 2
    ancho, alto = ANCHO_ICONO * escala, 18 * escala
    im = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 3 * escala, ancho - 1, alto - 4 * escala], radius=4 * escala,
                        fill=(0, 0, 0, 0), outline=(128, 128, 128, 90), width=escala)
    relleno = max(0, min(ancho - 2 * escala, int((ancho - 2 * escala) * pct / 100.0)))
    if relleno > 0:
        d.rounded_rectangle([escala, 4 * escala, escala + relleno, alto - 5 * escala],
                            radius=3 * escala, fill=color + (235,))
    fuente = None
    for ruta in ("/System/Library/Fonts/SFNSMono.ttf", "/System/Library/Fonts/Menlo.ttc",
                 "/System/Library/Fonts/Helvetica.ttc"):
        if os.path.exists(ruta):
            try:
                fuente = ImageFont.truetype(ruta, 11 * escala)
                break
            except Exception:
                continue
    if fuente is None:
        fuente = ImageFont.load_default()
    caja = d.textbbox((0, 0), texto, font=fuente)
    d.text(((ancho - (caja[2] - caja[0])) / 2, (alto - (caja[3] - caja[1])) / 2 - caja[1]), texto,
           font=fuente, fill=color + (255,))
    return im          # RGBA: si se pasa a RGB, lo transparente se vuelve NEGRO


def imagen_ns(texto, color, pct):
    im = imagen_icono(texto, color, pct)
    ruta = f"{AQUI}/logs/icono.png"
    im.save(ruta)
    datos_png = open(ruta, "rb").read()
    img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos_png, len(datos_png)))
    img.setSize_((ANCHO_ICONO, 18))
    return img


class VistaDesplegable(NSImageView):
    """La imagen responde a los clics de la barra de acciones de abajo."""

    def acceptsFirstMouse_(self, _e):
        return True

    def mouseDown_(self, evento):
        try:
            p = self.convertPoint_fromView_(evento.locationInWindow(), None)
            ancho, alto = self.frame().size.width, self.frame().size.height
            if p.y > alto - 46:                      # la fila de acciones (abajo del todo)
                x = p.x / max(1.0, ancho)
                zona = "Abrir panel" if x < 0.28 else ("Guia" if x < 0.45 else ("Refrescar" if x < 0.68 else "Salir"))
                self.accion_(zona)
        except Exception:
            pass

    def accion_(self, zona):
        dueno = getattr(self, "dueno", None)
        if dueno is not None:
            dueno.accion_(zona)


class Barra(NSObject):
    """Icono + desplegable. Sin WKWebView: dentro del bucle de la app peta.

    El desplegable es la vista del diseno renderizada a PNG (Chrome headless) y mostrada
    en un NSImageView. Se regenera cada vez que se abre, asi que los datos van en vivo.
    """

    def init(self):
        self = objc.super(Barra, self).init()
        if self is None:
            return None
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        boton = self.item.button()
        boton.setTarget_(self)
        boton.setAction_("pulsa:")
        log("icono listo")
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(8.0, self, "preparaImagenes:", None, False)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(300.0, self, "cadaCinco:", None, True)
        self.refresca_(None)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(60, self, "refresca:", None, True)
        return self

    def monta_popover(self):
        if getattr(self, "pop", None) is not None:
            return
        ancho, alto = 404, 664
        self.pop = NSPopover.alloc().init()
        self.pop.setContentSize_(NSMakeSize(ancho, alto))
        self.pop.setBehavior_(1)
        vc = NSViewController.alloc().init()
        vista = VistaDesplegable.alloc().initWithFrame_(NSMakeRect(0, 0, ancho, alto))
        vista.dueno = self
        vc.setView_(vista)
        self.pop.setContentViewController_(vc)
        self.vista = vista
        log("popover listo")

    def render(self, tema="claro"):
        """Regenera el HTML de ese tema y lo pasa a PNG. Se cachea: el clic no espera."""
        salida = f"{AQUI}/popover_{tema}.png"
        html = f"{AQUI}/popover{'_oscuro' if tema == 'oscuro' else ''}.html"
        subprocess.run([PY, f"{AQUI}/popover.py"] + (["--tema", "oscuro"] if tema == "oscuro" else []),
                       capture_output=True, timeout=120)
        bruto = f"{AQUI}/logs/_bruto_{tema}.png"
        subprocess.run([navegador(), "--headless=new",
                        "--hide-scrollbars", "--default-background-color=00000000",
                        "--force-device-scale-factor=2", f"--screenshot={bruto}",
                        "--window-size=404,1300", f"file://{html}"], capture_output=True, timeout=180)
        try:
            from PIL import Image, ImageDraw
            im = Image.open(bruto)  # (nada de convertir a RGB: rompe la transparencia)
            corte = im.size[1]
            for y in range(im.size[1] - 1, 0, -1):
                fila = [im.getpixel((x, y)) for x in range(0, im.size[0], 8)]
                # "contenido" = pixel oscuro o con color; el fondo claro del diseño no cuenta
                if any(min(q) < 205 or (max(q) - min(q)) > 25 for q in fila):
                    corte = min(im.size[1], y + 12)
                    break
            im.crop((0, 0, im.size[0], corte)).save(salida)
            log(f"recorte {tema}: {im.size[1] // 2} pt de alto")
        except Exception as e:
            log(f"no pude recortar: {type(e).__name__}: {e}")
        log(f"render {tema}: {os.path.getsize(salida) if os.path.exists(salida) else 0} B")
        return salida

    def alto_del_png(self, ruta):
        """El PNG ya viene recortado a su contenido: su alto es el del popover."""
        try:
            from PIL import Image
            im = Image.open(ruta)
            return max(200, min(1300, im.size[1] // 2))
        except Exception:
            return 664

    def ruta_del_tema(self):
        """Siempre el tema claro (el popover nativo se ve blanco, como Apple)."""
        ruta = f"{AQUI}/popover_claro.png"
        if not os.path.exists(ruta):
            self.render("claro")
        return ruta
    def accion_(self, zona):
        """Lo que hacen los botones del desplegable."""
        log(f"clic en: {zona}")
        try:
            if zona == "Abrir panel":
                subprocess.run(["open", "http://127.0.0.1:8765/"], capture_output=True)
            elif zona == "Refrescar":
                self.render("claro")
            elif zona == "Salir":
                self.pop.performClose_(None)
        except Exception as e:
            log(f"ERROR accion {type(e).__name__}: {e}")

    def pulsa_(self, sender):
        import time as _t
        _t0 = _t.time()
        try:
            self.monta_popover()
            if self.pop.isShown():
                self.pop.performClose_(sender)
                return
            ruta = self.ruta_del_tema()
            alto = self.alto_del_png(ruta)
            self.pop.setContentSize_(NSMakeSize(404, alto))
            self.vista.setFrame_(NSMakeRect(0, 0, 404, alto))
            datos_png = open(ruta, "rb").read()
            self.vista.setImage_(NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos_png, len(datos_png))))
            log(f"imagen del tema puesta ({alto} pt)")
            boton = self.item.button()
            self.pop.showRelativeToRect_ofView_preferredEdge_(boton.bounds(), boton, 1)
            log(f"desplegable mostrado en {_t.time()-_t0:.2f} s")
        except Exception:
            import traceback
            log("FALLO clic: " + traceback.format_exc()[-700:])

    def preparaImagenes_(self, _=None):
        """Los PNG de los dos temas, en segundo plano (para que el clic sea instantaneo)."""
        try:
            self.render("claro")
        except Exception as e:
            log(f"ERROR render {type(e).__name__}: {e}")

    def cadaCinco_(self, _=None):
        """Los PNG en segundo plano, para que el clic no espere nunca."""
        if getattr(self, "pop", None) is not None and self.pop.isShown():
            return
        self.preparaImagenes_()

    def refresca_(self, _=None):
        self.refresca()

    def refresca(self):
        try:
            d = datos()
            if not d.get("c5"):
                return
            n, u = trocea(d["c5"])
            self.item.button().setImage_(imagen_ns(f"{n:.1f}".replace(".", ",") + " " + u, color_magnitud(u), 40))
            self.item.button().setToolTip_(f"5 h: {d['c5']} - hoy {d.get('hoy','')} - {d.get('turnos','')} turnos")
            log(f"refresco: 5h {d['c5']}")
        except Exception as e:
            log(f"ERROR refresco {type(e).__name__}: {e}")


def main():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    barra = Barra.alloc().init()
    if "--abre" in sys.argv:
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1.5, barra, "pulsa:", None, False)
    log("entrando en el bucle")
    app.run()


if __name__ == "__main__":
    main()

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
import sys
import re
import subprocess
import sys

import objc
from AppKit import (
    NSAppearance,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSColor,
    NSEvent,
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

import costbar   # el motor: aqui solo por el servidor del panel (servir) y su puerto


AQUI = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable   # el mismo intérprete que corre esto
LOG = f"{AQUI}/logs/barra.log"
CONFIG = f"{AQUI}/config.json"

VIOLETA = (174, 0, 255)
ROSA = (255, 0, 140)
SUAVE = (154, 120, 214)
GRIS = (0, 0, 0, 40)
ANCHO_ICONO = 74

NAVEGADORES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)


def log(txt):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{subprocess.run(['date', '+%Y-%m-%d %H:%M:%S'], capture_output=True, text=True).stdout.strip()} {txt}\n")


def navegador():
    """El primero que haya instalado: Chrome, Chromium, Brave o Edge."""
    for ruta in NAVEGADORES:
        if os.path.exists(ruta):
            return ruta
    return None


def tema_preferido():
    """Lo que diga config.json ('claro' / 'oscuro' / 'sistema'); por defecto 'sistema'."""
    try:
        import json
        return (json.load(open(CONFIG)).get("tema") or "sistema").strip().lower()
    except Exception:
        return "sistema"


def tema_del_sistema():
    """Apariencia actual de macOS. Sin la clave (modo claro) 'defaults read' falla: es normal."""
    try:
        r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                           capture_output=True, text=True, timeout=5)
        return "oscuro" if r.stdout.strip().lower() == "dark" else "claro"
    except Exception:
        return "claro"


def tema_activo():
    pref = tema_preferido()
    return tema_del_sistema() if pref == "sistema" else pref


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
                        fill=(0, 0, 0, 0), outline=(0, 0, 0, 110), width=escala)
    relleno = max(0, min(ancho - 2 * escala, int((ancho - 2 * escala) * pct / 100.0)))
    if relleno > 0:
        d.rounded_rectangle([escala, 4 * escala, escala + relleno, alto - 5 * escala],
                            radius=3 * escala, fill=(0, 0, 0, 90))      # plantilla: solo cuenta el alfa
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
           font=fuente, fill=(0, 0, 0, 255))
    return im          # RGBA: si se pasa a RGB, lo transparente se vuelve NEGRO


def imagen_ns(texto, color, pct):
    im = imagen_icono(texto, color, pct)
    ruta = f"{AQUI}/logs/icono.png"
    im.save(ruta)
    datos_png = open(ruta, "rb").read()
    img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos_png, len(datos_png)))
    img.setSize_((ANCHO_ICONO, 18))
    img.setTemplate_(True)    # macOS lo pinta blanco o negro segun la barra, como sus propios iconos
    return img


class VistaDesplegable(NSImageView):
    """La imagen responde a los clics de la barra de acciones de abajo."""

    def acceptsFirstMouse_(self, _e):
        return True

    def hitTest_(self, _punto):
        # NSImageView no editable devuelve nil aqui y el clic se va al popover (que se cierra)
        return self

    def mouseDown_(self, evento):
        try:
            p = self.convertPoint_fromView_(evento.locationInWindow(), None)
            ancho = self.frame().size.width
            # la vista no esta volteada: y=0 es el borde INFERIOR. El PNG va recortado a la
            # tarjeta: abajo la fila de acciones (58 pt) y justo encima los chips de tema (36 pt).
            x = p.x / max(1.0, ancho)
            if p.y < 58:                   # cuatro columnas iguales dentro de la tarjeta
                self.accion_("Abrir panel" if x < 0.28 else ("Guia" if x < 0.51 else ("Refrescar" if x < 0.74 else "Salir")))
            elif p.y < 96:                 # tres chips: claro / oscuro / sistema
                self.accion_("tema:" + ("claro" if x < 0.36 else ("oscuro" if x < 0.66 else "sistema")))
        except Exception as e:
            log(f"ERROR clic {type(e).__name__}: {e}")

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
        # Comportamiento "definido por la app" (0), no "transitorio": el Chrome headless que
        # renderiza los PNG roba el foco un instante y un popover transitorio se cerraria solo.
        # El cierre por clic fuera lo hace este monitor (los clics dentro no pasan por el).
        self.pop.setBehavior_(0)
        self.monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            (1 << 1) | (1 << 3), lambda _e: self.pop.performClose_(None) if self.pop.isShown() else None)
        vc = NSViewController.alloc().init()
        vista = VistaDesplegable.alloc().initWithFrame_(NSMakeRect(0, 0, ancho, alto))
        vista.setImageScaling_(3)          # NSImageScaleAxesIndependently: el PNG llena la vista
        vista.setImageAlignment_(0)
        vista.setAutoresizingMask_(18)     # ancho y alto siguen al contenido del popover
        vista.dueno = self
        vc.setView_(vista)
        self.pop.setContentViewController_(vc)
        self.vista = vista
        log("popover listo")

    def render(self, tema=None):
        """Regenera los DOS temas de una vez (un solo escaneo) y recorta cada PNG a la tarjeta.

        Asi cambiar de tema es instantaneo: solo se cambia de imagen. El argumento se acepta
        por compatibilidad; devuelve la ruta del tema pedido (o del activo).
        """
        chrome = navegador()
        if chrome is None:
            log("ERROR render: no hay Chrome, Chromium, Brave ni Edge instalado")
            return f"{AQUI}/popover_{tema or tema_activo()}.png"
        subprocess.run([PY, f"{AQUI}/popover.py"], capture_output=True, timeout=120)
        for t in ("claro", "oscuro"):
            html = f"{AQUI}/popover{'_oscuro' if t == 'oscuro' else ''}.html"
            bruto = f"{AQUI}/logs/_bruto_{t}.png"
            subprocess.run([chrome, "--headless=new", "--hide-scrollbars",
                            "--default-background-color=00000000", "--force-device-scale-factor=2",
                            f"--screenshot={bruto}", "--window-size=404,1400", f"file://{html}"],
                           capture_output=True, timeout=180)
            try:
                from PIL import Image
                im = Image.open(bruto).convert("RGBA")
                caja = im.getchannel("A").getbbox()     # la tarjeta es lo unico opaco
                if caja:
                    im = im.crop(caja)
                im.save(f"{AQUI}/popover_{t}.png")
                log(f"render {t}: {im.size[0] // 2}x{im.size[1] // 2} pt")
            except Exception as e:
                log(f"ERROR recorte {t}: {type(e).__name__}: {e}")
        return f"{AQUI}/popover_{tema or tema_activo()}.png"

    def tamano_del_png(self, ruta):
        """El PNG ya viene recortado a la tarjeta: su tamano (a 2x) es el del popover."""
        try:
            from PIL import Image
            im = Image.open(ruta)
            return (max(200, im.size[0] // 2), max(200, min(1400, im.size[1] // 2)))
        except Exception:
            return (384, 664)

    def ruta_del_tema(self):
        """Claro, oscuro o el que tenga macOS ahora mismo, segun config.json ('tema')."""
        tema = tema_activo()
        ruta = f"{AQUI}/popover_{tema}.png"
        if not os.path.exists(ruta):
            self.render(tema)
        return ruta

    def accion_(self, zona):
        """Lo que hacen los botones del desplegable."""
        log(f"clic en: {zona}")   # las zonas se reparten en VistaDesplegable.mouseDown_
        try:
            if zona == "Abrir panel":
                subprocess.run(["open", f"http://127.0.0.1:{costbar.PUERTO}/"], capture_output=True)
            elif zona == "Guia":
                subprocess.run(["open", f"{AQUI}/GUIA.md"], capture_output=True)
            elif zona == "Refrescar":
                self.repinta()
            elif zona.startswith("tema:"):
                import json
                c = json.load(open(CONFIG)) if os.path.exists(CONFIG) else {}
                c["tema"] = zona[5:]
                json.dump(c, open(CONFIG, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
                self.muestra()          # instantaneo: los dos temas ya estan renderizados
                NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(0.5, self, "preparaImagenes:", None, False)
            elif zona == "Salir":
                self.pop.performClose_(None)
        except Exception as e:
            log(f"ERROR accion {type(e).__name__}: {e}")

    def viste_popover(self, tema):
        """El marco nativo del popover va a juego con el PNG (si no, queda un borde blanco)."""
        nombre = "NSAppearanceNameDarkAqua" if tema == "oscuro" else "NSAppearanceNameAqua"
        self.pop.setAppearance_(NSAppearance.appearanceNamed_(nombre))

    def muestra(self):
        """Pone en el desplegable el PNG del tema activo, al tamano exacto de la tarjeta."""
        tema = tema_activo()
        self.viste_popover(tema)
        ruta = self.ruta_del_tema()
        ancho, alto = self.tamano_del_png(ruta)
        self.pop.setContentSize_(NSMakeSize(ancho, alto))
        self.vista.setFrameSize_(NSMakeSize(ancho, alto))   # solo el tamano: el popover la coloca (con su margen)
        datos_png = open(ruta, "rb").read()
        img = NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(datos_png, len(datos_png)))
        img.setSize_((ancho, alto))        # el PNG va a 2x: su tamano en puntos es la mitad
        self.vista.setImage_(img)
        return ancho, alto

    def repinta(self):
        """Refrescar: datos nuevos (escaneo + Chrome, tarda unos segundos) y a la vista."""
        self.render()
        self.muestra()

    def pulsa_(self, sender):
        import time as _t
        _t0 = _t.time()
        try:
            self.monta_popover()
            if self.pop.isShown():
                self.pop.performClose_(sender)
                return
            ancho, alto = self.muestra()
            log(f"imagen del tema puesta ({ancho}x{alto} pt)")
            boton = self.item.button()
            self.pop.showRelativeToRect_ofView_preferredEdge_(boton.bounds(), boton, 1)
            log(f"desplegable mostrado en {_t.time()-_t0:.2f} s")
        except Exception:
            import traceback
            log("FALLO clic: " + traceback.format_exc()[-700:])

    def preparaImagenes_(self, _=None):
        """Los PNG de los dos temas, en segundo plano (para que el clic sea instantaneo)."""
        try:
            self.render()
            if getattr(self, "pop", None) is not None and self.pop.isShown():
                self.muestra()          # si esta abierto, que luzca lo recien renderizado
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
    costbar.servir()          # el panel y su configurador, en un hilo aparte
    if "--abre" in sys.argv:
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(1.5, barra, "pulsa:", None, False)
    log("entrando en el bucle")
    app.run()


if __name__ == "__main__":
    main()

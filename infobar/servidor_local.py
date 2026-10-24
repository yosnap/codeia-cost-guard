"""Servidor local del panel y su configurador (http://127.0.0.1:PUERTO).

Solo escucha en 127.0.0.1, pero eso no basta: cualquier web abierta en el navegador puede pedirle
cosas a localhost. Por eso:
  · toda peticion con una cabecera Host que no sea 127.0.0.1/localhost:PUERTO se rechaza (corta el
    DNS rebinding: evil.com resuelto a 127.0.0.1 llega con Host: evil.com);
  · el POST solo va a /guardar, con Content-Type application/json y Origin exacto del panel (un
    POST "simple" desde otra web no puede poner ese tipo sin preflight, ni falsear Origin);
  · lo que se guarda se FUSIONA con config.json (nunca lo reemplaza) y se valida;
  · las respuestas nunca llevan claves (lista cerrada de nombres);
  · **sesion obligatoria en todo** (lectura incluida): Host/Origin frenan a las webs del navegador, pero no a
    otro proceso del Mac. La barra abre el panel con un ticket de un solo uso (en memoria, 60 s) que el servidor
    cambia por una cookie HttpOnly SameSite=Strict; sin la cookie, nada responde. El ticket no toca el disco.
"""
import hmac, json, os, secrets, socket, subprocess, sys, threading, time, webbrowser

from urllib.parse import parse_qs, urlsplit

from . import ajustes, claves, exportacion, exportacion_csv, guia_web, historico, presupuestos, rutas, servidores_alta, servidores_alta_claves, servidores_api, servidores_avisos, servidores_panel, servidores_webs
from . import cuota_nan, servidores_ciclo
from .agregacion import informe
from .config_y_estado import cfg, guardar_con_revision, guardar_config, log, olvidar_cache
from .servidores import _errores_ips_guardadas, errores_ips, errores_servidores, revision_servidores


def _estado_copias():
    """Estado de las copias cifradas para Ajustes: se calcula aquí porque el informe desde logs no lo trae."""
    try:
        return exportacion.estado_copias(cfg().get("copias"))
    except Exception:
        return None


def html_con_nonce(html):
    """El panel solo ejecuta su único <script>, marcado con un nonce nuevo en cada respuesta; sin manejadores en línea ni nada externo."""
    nonce = secrets.token_urlsafe(18)
    html = html.replace(b"<script>", f'<script nonce="{nonce}">'.encode(), 1)
    return html, (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; img-src 'self' data:; "
                  "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _estado_presupuestos():
    try:
        return presupuestos.evaluar()
    except Exception as e:
        log(f"presupuestos: {type(e).__name__}")
        return {"disponible": False, "motivo": "No se pudieron evaluar los presupuestos.", "estados": []}


PUERTO = 47391              # panel/configurador local: un puerto alto que casi nada usa
# campos que nunca salen por HTTP ni se aceptan desde el panel: nombres exactos y sufijos con '_'.
# Los limite_*_tokens son cuotas (acaban en _tokens, no en _token): por eso no vale un patron *token*
SECRETOS = {"api_key", "apikey", "token", "password", "secret", "clave", "authorization", "private_key", "contrasena", "contraseña", "passwd", "pass"}
SUFIJOS_SECRETOS = ("_key", "_token", "_secret", "_password")
MAX_CUERPO = 1_000_000      # bytes: la config cabe de sobra
COOKIE = "cg_sesion"          # el nombre real lleva el puerto: dos instancias en 127.0.0.1 no se pisan
TICKET_VIDA_S = 60
SESION_VIDA_S = 12 * 3600
MAX_SESIONES = 20
_SERVIDOR = []              # el servidor que arranco servir(): url_panel() le pide los tickets


def nuevo_ticket(srv):
    """Ticket de un solo uso para abrir el panel; vive en memoria y caduca a los 60 s."""
    ahora = time.time()
    with srv.cerrojo:
        for t in [t for t, exp in srv.tickets.items() if exp < ahora]:
            del srv.tickets[t]
        t = secrets.token_urlsafe(24)
        srv.tickets[t] = ahora + TICKET_VIDA_S
    return t


def _canjea(srv, ticket):
    """Cambia un ticket valido por una sesion nueva (o None). El ticket se gasta aunque falle lo demas."""
    ahora = time.time()
    with srv.cerrojo:
        exp = srv.tickets.pop(ticket, 0)
        if exp < ahora:
            return None
        for sid in [k for k, e in srv.sesiones.items() if e < ahora]:
            del srv.sesiones[sid]
        while len(srv.sesiones) >= MAX_SESIONES:
            del srv.sesiones[min(srv.sesiones, key=srv.sesiones.get)]
        sid = secrets.token_urlsafe(32)
        srv.sesiones[sid] = ahora + SESION_VIDA_S
        return sid


def nombre_cookie(srv):
    return f"{COOKIE}_{srv.server_address[1]}"


def _valor_cookie(cabecera, nombre):
    """El valor de la cookie `nombre` en la cabecera Cookie, leida a mano: el parser estandar se rinde en la
    primera cookie ajena mal formada (otra app en 127.0.0.1 puede dejarlas) y bloquearia al usuario."""
    for trozo in (cabecera or "").split(";"):
        k, _, v = trozo.strip().partition("=")
        if k == nombre:
            return v.strip()
    return ""


def _sesion_valida(srv, cabecera_cookie):
    valor = _valor_cookie(cabecera_cookie, nombre_cookie(srv)).encode("utf-8", "replace")
    if not valor:
        return False
    ahora, ok = time.time(), False
    with srv.cerrojo:
        for sid, exp in srv.sesiones.items():          # sin cortocircuito: tiempo constante frente al numero de sesiones
            ok |= hmac.compare_digest(sid.encode(), valor) and exp >= ahora
    return ok


DESTINOS = {"guia": "/guia"}          # a dónde puede mandar `entrar` tras canjear el ticket (lista cerrada)


def url_panel(destino=None):
    """URL para abrir el panel en el navegador, con un ticket de un solo uso si el servidor esta en marcha. `destino`: una clave de DESTINOS."""
    base = f"http://127.0.0.1:{PUERTO}/"
    if _SERVIDOR:
        return f"{base}entrar?t={nuevo_ticket(_SERVIDOR[0])}" + (f"&ir={destino}" if destino in DESTINOS else "")
    return base + (DESTINOS[destino].lstrip("/") if destino in DESTINOS else "")


def es_secreto(nombre):
    n = str(nombre).lower()
    return n in SECRETOS or n.endswith(SUFIJOS_SECRETOS)


RUTAS_ALTA = {f"/servidores/alta/{a}": a for a in ("diagnostico", "huella", "instalar", "comprobar")}


def con_revision(c):
    """La configuración sin secretos más la huella de su lista de servidores (`servidores_rev`) que la página devuelve al guardar."""
    c = dict(c)
    c["servidores_rev"] = revision_servidores(c.get("servidores"))
    return c


def sin_secretos(o):
    """Copia de `o` sin los campos de SECRETOS (a cualquier profundidad)."""
    if isinstance(o, dict):
        return {k: sin_secretos(v) for k, v in o.items() if not es_secreto(k)}
    if isinstance(o, list):
        return [sin_secretos(v) for v in o]
    return o


def errores_config(nuevo):
    """Errores de una config recibida del panel; vacia si se puede guardar."""
    if not isinstance(nuevo, dict):
        return ["no es un objeto"]
    errores = []
    provs, alias = nuevo.get("proveedores"), nuevo.get("aliases")
    if not isinstance(provs, list) or not isinstance(alias, dict):
        return ["faltan campos (proveedores / aliases)"]
    for i, p in enumerate(provs):
        if not (isinstance(p, dict) and isinstance(p.get("nombre"), str) and p["nombre"].strip()):
            errores.append(f"proveedores[{i}]: sin nombre")
            continue
        for k, v in p.items():
            if k.startswith("limite_") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
                errores.append(f"proveedores[{i}].{k}: debe ser un numero >= 0")
        if not isinstance(p.get("modelos", []), list):
            errores.append(f"proveedores[{i}].modelos: debe ser una lista")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in alias.items()):
        errores.append("aliases: claves y valores deben ser texto")
    for k, v in nuevo.items():
        if k.startswith("limite_") and not (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0):
            errores.append(f"{k}: debe ser un numero >= 0")
    errores += errores_servidores(nuevo.get("servidores"))
    errores += _errores_ips_guardadas(nuevo, "config")
    errores += errores_ips(nuevo.get("ips_conocidas"))
    return errores


def _cuenta_panel(s):
    """Cuenta del Llavero de la clave del panel (Dokploy) de un servidor, o ''."""
    from .servidores_panel import cuenta
    return cuenta(s)


def crear_servidor(puerto=PUERTO):
    """El servidor HTTP (sin arrancar). servir() lo lanza en un hilo; los tests usan un puerto libre."""
    import http.server, socketserver

    base = rutas.RAIZ
    historiando = threading.BoundedSemaphore(2)        # como mucho dos consultas de histórico a la vez
    detallando = threading.BoundedSemaphore(1)         # y una del detalle de servidor, aparte
    generando = threading.Lock()        # un solo panel.py a la vez aunque lleguen muchas peticiones

    class H(http.server.BaseHTTPRequestHandler):
        timeout = 15            # un cliente que anuncia un cuerpo y no lo manda no retiene el hilo

        def log_message(self, *a):
            pass

        def _hosts(self):
            p = self.server.server_address[1]
            return {f"127.0.0.1:{p}", f"localhost:{p}"}

        def _manda(self, codigo, cuerpo, tipo, csp=None):
            self.send_response(codigo)
            # todo lo que no es el panel no necesita cargar nada; el panel solo ejecuta su script con nonce
            self.send_header("Content-Security-Policy", csp or "default-src 'none'; frame-ancestors 'none'")
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            for k, v in getattr(self, "_extra", []):
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(cuerpo)

        def _json(self, codigo, datos):
            self._manda(codigo, json.dumps(datos, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _host_ok(self):
            # Host exacto (DNS rebinding) y nada que venga lanzado desde otra web (un <img> a
            # localhost no lee la respuesta, pero si le haria escanear o generar el panel)
            if (self.headers.get("Host") or "") in self._hosts() and \
                    (self.headers.get("Sec-Fetch-Site") or "same-origin") in ("same-origin", "none"):
                return True
            self._manda(403, b"origen no permitido", "text/plain")
            return False

        def _sesion_ok(self):
            if _sesion_valida(self.server, self.headers.get("Cookie")):
                return True
            self._manda(401, "Sin sesión: abre el panel desde el icono de CostGuard en la barra de menús.".encode(),
                        "text/plain; charset=utf-8")
            return False

        def _entrar(self):
            from urllib.parse import parse_qs, urlsplit
            q = parse_qs(urlsplit(self.path).query)
            t = (q.get("t") or [""])[0]
            destino = DESTINOS.get((q.get("ir") or [""])[0], "/")
            sid = _canjea(self.server, t)
            if not sid:
                return self._manda(401, "Ticket no válido o caducado: abre el panel desde la barra de menús.".encode(),
                                   "text/plain; charset=utf-8")
            self._extra = [("Set-Cookie", f"{nombre_cookie(self.server)}={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESION_VIDA_S}"),
                           ("Location", destino)]
            self._manda(302, b"", "text/plain")

        def _historico(self, como_csv=False):
            """GET /historico?desde=&hasta=&paso=&agrupa= : solo esos cuatro parámetros, una vez cada uno, validados en `historico`.
            Con `como_csv` devuelve el CSV como descarga: se genera en memoria y el servidor no deja ningún fichero."""
            try:
                q = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=8)
            except ValueError:
                return self._json(400, {"error": "parámetros no permitidos"})
            if set(q) - {"desde", "hasta", "paso", "agrupa"} or any(len(v) != 1 for v in q.values()):
                return self._json(400, {"error": "parámetros no permitidos"})
            ok, motivo = historico.disponible()
            if not ok:
                return self._json(200, {"disponible": False, "motivo": motivo})
            if not historiando.acquire(blocking=False):
                return self._json(429, {"error": "hay otras consultas en curso; vuelve a intentarlo"})
            try:
                g = {k: v[0] for k, v in q.items()}
                r = historico.historico(g.get("desde"), g.get("hasta"), g.get("paso", "dia"), g.get("agrupa", "ninguno"))
                if como_csv:
                    datos = exportacion_csv.construir(r)
                    exportacion_csv.auditar(r, datos.count(b"\r\n") - 1, False, "panel")
                    self._extra = [("Content-Disposition", f'attachment; filename="{exportacion_csv.nombre(r)}"')]
                    return self._manda(200, datos, "text/csv; charset=utf-8")
                self._json(200, {"disponible": True, **r})
            except exportacion_csv.ErrorCsv as e:
                self._json(400, {"error": str(e)})
            except historico.ErrorHistorico as e:
                self._json(400, {"error": str(e)})
            except historico.SinAlmacen as e:
                self._json(503, {"error": str(e)})
            except Exception as e:
                log(f"historico: {type(e).__name__}")
                self._json(500, {"error": "no se pudo calcular el histórico"})
            finally:
                historiando.release()

        def _servidores(self, ruta):
            """GET /servidores/historia y /servidores/ips (solo lectura; las IPs solo con servidor= y con_ips=1)."""
            try:
                q = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=6)
            except ValueError:
                return self._json(400, {"error": "parámetros no permitidos"})
            plaza = detallando if ruta == "/servidores/detalle" else historiando        # el detalle no compite con el histórico por sus dos plazas
            if not plaza.acquire(blocking=False):
                return self._json(429, {"error": "hay otras consultas en curso; vuelve a intentarlo"})
            try:
                self._json(*servidores_api.atiende(ruta, q))
            finally:
                plaza.release()

        def do_GET(self):
            if not self._host_ok():
                return
            ruta = self.path.split("?")[0]
            if ruta == "/entrar":
                return self._entrar()
            if not self._sesion_ok():
                return
            if ruta in ("/", "/panel.html"):
                try:
                    # se regenera al abrir: asi el panel siempre lleva los datos de ahora
                    with generando:
                        subprocess.run([sys.executable, f"{base}/panel.py"], capture_output=True, timeout=120)
                    html, csp = html_con_nonce(open(f"{base}/panel.html", "rb").read())
                    self._manda(200, html, "text/html; charset=utf-8", csp=csp)
                except Exception as e:
                    self._manda(500, str(e).encode(), "text/plain")
            elif ruta == "/navegador":
                # el navegador del sistema no comparte cookies con esta ventana (ni con la de otro SO):
                # se le abre SU PROPIO ticket de un solo uso, igual que hace el icono de la bandeja
                try:
                    puerto = self.server.server_address[1]
                    t = nuevo_ticket(self.server)
                    webbrowser.open(f"http://127.0.0.1:{puerto}/entrar?t={t}")
                    self._json(200, {"ok": True})
                except Exception as e:
                    self._json(500, {"ok": False, "error": str(e)[:200]})
            elif ruta == "/guia":
                try:
                    with open(f"{base}/GUIA.md", encoding="utf-8") as f:
                        md = f.read(200_000)
                except OSError:
                    return self._manda(404, "No encuentro GUIA.md.".encode(), "text/plain; charset=utf-8")
                self._manda(200, guia_web.documento(md).encode("utf-8"), "text/html; charset=utf-8", csp=guia_web.CSP)
            elif ruta == "/historico":
                self._historico()
            elif ruta == "/historico.csv":
                self._historico(como_csv=True)
            elif ruta in ("/servidores/historia", "/servidores/ips", "/servidores/detalle"):
                self._servidores(ruta)
            elif ruta == "/servidores/webs/detectadas":
                try:
                    q = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=3)
                except ValueError:
                    return self._json(400, {"ok": False})
                ids = q.get("servidor") or []
                if set(q) != {"servidor"} or len(ids) != 1:
                    return self._json(400, {"ok": False, "codigo": "entrada_invalida"})
                self._json(*servidores_webs.atiende_detectadas(ids[0]))
            elif ruta == "/servidores/alta/estado":
                try:
                    q = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=3)
                except ValueError:
                    return self._json(400, {"ok": False})
                ids = q.get("id") or []
                if set(q) != {"id"} or len(ids) != 1:
                    return self._json(400, {"ok": False, "codigo": "entrada_invalida"})
                self._json(*servidores_alta.atiende("estado", {"id": ids[0]}))
            elif ruta == "/origenes":
                try:
                    r = informe()
                    self._json(200, {"origenes": r.get("origenes", {}), "perfiles_hermes": r.get("perfiles_hermes", {}), "copias": _estado_copias(),
                                     "planes": [p["nombre"] for p in (cfg().get("proveedores") or [])],
                                     "config": con_revision(sin_secretos(cfg()))})
                except Exception as e:
                    self._json(500, {"error": str(e)})
            elif ruta == "/ajustes":
                try:
                    r = informe()
                    c = con_revision(sin_secretos(cfg()))
                    provs = [{"nombre": p["nombre"], "clave_guardada": bool(claves.leer_clave(ajustes.cuenta_de(p["nombre"], p.get("uso_api")))),
                              "api_soportada": ajustes.api_soportada(p)} for p in (c.get("proveedores") or [])]
                    # claves de los paneles (Dokploy/Easypanel) de cada servidor, por host
                    paneles = {}
                    for s in c.get("servidores") or []:
                        cuenta = _cuenta_panel(s)
                        if cuenta:
                            paneles[s["nombre"]] = {"cuenta": cuenta, "clave_guardada": bool(claves.leer_clave(cuenta))}
                    self._json(200, {"config": c, "origenes": r.get("origenes", {}), "proyectos": r.get("proyectos_origen", {}),
                                     "proveedores": provs, "paneles": paneles, "keychain": claves.hay_llavero(), "copias": _estado_copias(), "presupuestos": _estado_presupuestos(),
                                     "alta_servidor": "infobar/remoto/alta-servidor.sh",
                                     # claves de ~/.ssh que se pueden elegir para el alta: solo NOMBRES de fichero, nunca su contenido
                                     "claves_ssh": servidores_alta_claves.listar(),
                                     # versión del script de lectura en cada servidor (de la última lectura) y la que trae esta app
                                     "scripts": {"ultima": servidores_alta.version_script(),
                                                 "servidores": {n: (e.get("datos") or {}).get("version")
                                                                for n, e in (servidores_ciclo.leer_cache().get("servidores") or {}).items() if isinstance(e, dict)}}})
                except Exception as e:
                    self._json(500, {"error": str(e)[:200]})
            elif ruta == "/config.json":
                # el fichero tal cual (sin los valores por defecto que mete cfg()), sin secretos
                try:
                    with open(rutas.CONFIG, encoding="utf-8") as f:
                        self._json(200, sin_secretos(json.load(f)))
                except (OSError, ValueError):
                    self._json(200, {})
            else:
                self._manda(404, b"no", "text/plain")

        def _lee_cuerpo(self):
            """Lee (y asi descarta) el cuerpo declarado por Content-Length antes de poder rechazar la
            peticion: en Windows, cerrar el socket con bytes sin leer manda RST en vez de FIN, y el
            cliente ve un ConnectionResetError aunque la respuesta ya se hubiera enviado entera."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            return self.rfile.read(min(n, MAX_CUERPO + 1)) if n > 0 else b""

        def _cuerpo_json(self, crudo):
            """El JSON de `crudo` (ya leido por _lee_cuerpo), tras pasar la puerta (Content-Type, Origin
            exacto, tamaño); None si ya respondio."""
            tipo = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            origen = self.headers.get("Origin") or ""
            if tipo != "application/json" or origen not in {f"http://{h}" for h in self._hosts()}:
                self._json(403, {"ok": False, "error": "origen o tipo no permitido"})
                return None
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if not 0 < n <= MAX_CUERPO:
                self._json(413, {"ok": False, "error": "cuerpo vacio o demasiado grande"})
                return None
            try:
                return json.loads(crudo.decode("utf-8"))
            except ValueError:
                self._json(400, {"ok": False, "error": "JSON no valido"})
                return None

        def do_POST(self):
            crudo = self._lee_cuerpo()
            if not self._host_ok() or not self._sesion_ok():
                return
            ruta = self.path.split("?")[0]
            if ruta not in ("/guardar", "/clave", "/servidores/webs", "/servidores/avisos/probar", "/servidores/panel/probar") and ruta not in RUTAS_ALTA:
                return self._manda(404, b"no", "text/plain")
            nuevo = self._cuerpo_json(crudo)
            if nuevo is None:
                return
            try:
                if ruta == "/clave":
                    return self._clave(nuevo)
                if ruta == "/servidores/webs":
                    return self._json(*servidores_webs.atiende_cambio(nuevo))
                if ruta == "/servidores/avisos/probar":
                    return self._json(*servidores_avisos.probar(nuevo))
                if ruta == "/servidores/panel/probar":
                    return self._json(*servidores_panel.probar(nuevo))
                if ruta in RUTAS_ALTA:
                    return self._json(*servidores_alta.atiende(RUTAS_ALTA[ruta], nuevo))
                errores = errores_config(nuevo)
                if errores:
                    return self._json(400, {"ok": False, "error": "; ".join(errores[:5])})
                rev = nuevo.pop("servidores_rev", None)
                try:
                    with open(rutas.CONFIG, encoding="utf-8") as f:
                        en_disco = json.load(f)
                except (OSError, ValueError):
                    en_disco = {}
                cuentas_antes = {_cuenta_panel(s) for s in cfg().get("servidores") or [] if isinstance(s, dict)} - {""}
                parche = sin_secretos(ajustes.normalizar(nuevo, en_disco, cfg().get("servidores")))
                # la revisión se comprueba y la lista se escribe bajo el cerrojo de servidores.json (el mismo que usa un alta): un alta a la vez no se pierde
                if not guardar_con_revision(parche, lambda lista: "servidores" not in nuevo or rev == revision_servidores(sin_secretos(lista))):
                    # la lista cambió desde que la página la leyó (un alta, otra pestaña): guardar la copia vieja la pisaría
                    return self._json(409, {"ok": False, "conflicto": True, "error": "la lista de servidores cambió: recarga la página antes de guardar"})
                nueva_rev = revision_servidores(sin_secretos(cfg().get("servidores")))
                if "servidores" in parche:           # la clave de un panel que se quita o cambia de dirección no se queda huérfana en el Llavero
                    for cuenta in cuentas_antes - {_cuenta_panel(s) for s in cfg().get("servidores") or [] if isinstance(s, dict)}:
                        try:
                            claves.borrar_clave(cuenta)
                            log("clave de un panel borrada del Llavero (el servidor o su panel ya no están)")
                        except Exception as e:
                            log(f"no se pudo borrar una clave huérfana ({type(e).__name__})")
                try:
                    from . import webs_sondeo
                    webs_sondeo.olvidar(servidores=cfg().get("servidores") or [])     # las webs que ya no están en la lista pierden su estado ahora
                    from . import servidores_registro
                    servidores_registro.sincronizar_webs()                              # y su seguimiento en la base
                except Exception as e:
                    log(f"webs: no se pudo podar el estado ({type(e).__name__})")
                try:
                    olvidar_cache()                    # el plan de cada fichero cacheado depende de la config
                except OSError:
                    pass
                log("configuracion guardada desde el panel")
                self._json(200, {"ok": True, "servidores_rev": nueva_rev})
            except Exception as e:
                self._json(400, {"ok": False, "error": str(e)[:200]})

        def _clave(self, cuerpo):
            """Guarda o borra una clave en el Llavero. Solo cuentas de proveedores o paneles que ya estan en la config."""
            if not isinstance(cuerpo, dict):
                return self._json(400, {"ok": False, "error": "no es un objeto"})
            c = cfg()
            provs = {p["nombre"]: p for p in c.get("proveedores") or []}
            conocidas = {ajustes.cuenta_de(n, p.get("uso_api")): n for n, p in provs.items()}
            conocidas.update({_cuenta_panel(s): s["nombre"] for s in c.get("servidores") or [] if _cuenta_panel(s)})
            nombre = str(cuerpo.get("proveedor") or "")
            cuenta = cuerpo.get("cuenta") or ajustes.cuenta_de(nombre, (provs.get(nombre) or {}).get("uso_api"))
            if cuenta not in conocidas or not claves.cuenta_valida(cuenta):
                return self._json(400, {"ok": False, "error": "cuenta desconocida: añade antes el proveedor o servidor"})
            if cuerpo.get("borrar"):
                claves.borrar_clave(cuenta)
            else:
                claves.guardar_clave(cuenta, str(cuerpo.get("clave") or ""))
            if cuenta == "nan":
                cuota_nan.invalidar()                  # que la cache de la API no oculte la clave nueva
            log(f"clave de {conocidas[cuenta]} {'borrada' if cuerpo.get('borrar') else 'guardada'} (Llavero)")
            self._json(200, {"ok": True})

    class S(socketserver.ThreadingTCPServer):
        # En Windows SO_REUSEADDR deja que un segundo proceso escuche en un puerto ya ocupado y las conexiones se repartan
        # entre los dos (tickets y sesiones que fallan a ratos): allí el puerto se pide en exclusiva.
        allow_reuse_address = os.name != "nt"
        daemon_threads = True

        def server_bind(self):
            if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    srv = S(("127.0.0.1", puerto), H)
    srv.tickets, srv.sesiones, srv.cerrojo = {}, {}, threading.Lock()
    return srv


def servir():
    """Sirve el panel en http://127.0.0.1:PUERTO y guarda la configuracion que envies desde el.

    Asi no hay que editar ficheros a mano: el propio panel tiene el configurador.
    """
    def arranca():
        # si el puerto esta ocupado (otra copia que aun se esta cerrando), se reintenta cada 30 s
        # en vez de quedarse sin panel hasta el siguiente reinicio
        avisado = False
        while True:
            try:
                srv = crear_servidor(PUERTO)
            except OSError as e:
                if not avisado:
                    log(f"configurador no disponible ({e}); reintento cada 30 s")
                    avisado = True
                time.sleep(30)
                continue
            log(f"configurador listo en http://127.0.0.1:{PUERTO}")
            _SERVIDOR[:] = [srv]
            srv.serve_forever()
            return

    threading.Thread(target=arranca, name="infobar-panel", daemon=True).start()

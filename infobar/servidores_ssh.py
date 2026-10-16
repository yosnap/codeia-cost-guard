"""Conexion SSH a un servidor y lectura de la salida de infobar-recoger.

La clave dedicada solo puede ejecutar el script del servidor (command= en authorized_keys), asi que
aqui no se envia ninguna orden: se conecta y se lee. Todo con tope de tiempo y de tamano, y sin
heredar nada peligroso de ~/.ssh/config (ProxyCommand, LocalCommand, reenvios).
"""
import math, os, re, subprocess, time

from . import plataforma, servidores_intentos, servidores_inventario, servidores_webs
from .servidores import errores_servidores

CLAVE = os.path.expanduser("~/.ssh/infobar_ed25519")
TOPE_S = 20                 # segundos por servidor, conexion incluida
TOPE_BYTES = 128 * 1024     # lo que se pasa de aqui se corta (las secciones de v1 van primero y caben): no se tira la lectura entera
SECCIONES = {"version", "sistema", "memoria", "disco", "sesiones", "puertos", "intentos",
             "actualizaciones", "bastionado", "servicios", "contenedores", "bastionado_root", "fail2ban", "version_root", "fin_root", "inventario", "webs", "correo", "espera"}
SECCIONES_ROOT = ("servicios", "contenedores", "bastionado_root", "fail2ban", "inventario", "webs")     # las que escribe recoger-root
MARCADAS = ("intentos", "inventario", "webs")    # secciones de v2 con marco: "n <lineas>" al principio y "fin <lineas> <codigo>" al final
_MARCO_N = re.compile(r"n ([0-9]{1,6})")
_MARCO_FIN = re.compile(r"fin ([0-9]{1,6}) ([0-9]{1,3})")


OPCIONES_SEGURAS = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "ServerAliveInterval=3", "-o", "ServerAliveCountMax=2",
                    "-o", "StrictHostKeyChecking=yes", "-o", "ForwardAgent=no", "-o", "ForwardX11=no",
                    "-o", "ClearAllForwardings=yes", "-o", "PermitLocalCommand=no", "-o", "ProxyCommand=none",
                    "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "LogLevel=ERROR")


def orden_ssh(servidor):
    """La orden ssh (lista, sin shell). Revalida el servidor justo antes de usarlo."""
    if errores_servidores([servidor]):
        raise ValueError("servidor con configuracion no valida")
    puerto = int(servidor.get("puerto") or 22)
    if not 0 < puerto < 65536:
        raise ValueError("puerto no valido")
    return ["ssh", "-T", "-p", str(puerto), "-i", CLAVE, "-o", "IdentitiesOnly=yes", *OPCIONES_SEGURAS, "--", servidor["ssh"]]


def clasifica_error(codigo, stderr):
    """Motivo legible de un fallo de ssh, para la tarjeta del servidor."""
    e = (stderr or "").lower()
    if "host key verification failed" in e or "no matching host key" in e or "host key for" in e:
        return "clave de host no confiada"
    if "permission denied" in e or "too many authentication failures" in e:
        return "autenticacion"
    if "timed out" in e or "timeout" in e:
        return "timeout"
    if "could not resolve" in e or "no route" in e or "network is unreachable" in e or "refused" in e:
        return "red"
    if "identity file" in e and "not accessible" in e:
        return "falta la clave ~/.ssh/infobar_ed25519"
    return f"ssh salio con {codigo}"


TOPE_ERR = 8 * 1024


def _lee_con_tope(flujo, tope, destino, excedido):
    """Lee `flujo` por trozos y se queda con `tope` bytes como mucho. Si el otro lado manda mas,
    marca `excedido` (el hilo principal mata ssh) y sigue leyendo para descartar, sin acumular."""
    total = 0
    for trozo in iter(lambda: flujo.read(4096), b""):
        if total < tope:
            destino.append(trozo[:tope - total])
        total += len(trozo)
        if total > tope:
            excedido.set()
    flujo.close()


def _mata(p):
    plataforma.matar_arbol(p)                  # el grupo entero: no quedan ssh huerfanos


def ejecutar(servidor):
    """(salida, error): salida es el texto del script; error "" si fue bien.

    La salida se lee mientras llega y con tope (TOPE_BYTES): un servidor que mande de mas no puede
    llenar la memoria antes de recortar. Si se pasa del tope, se corta la conexion."""
    import threading
    try:
        orden = orden_ssh(servidor)
    except ValueError as e:
        return "", str(e)
    try:
        p = subprocess.Popen(orden, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, **plataforma.grupo_nuevo_kwargs())
    except OSError as e:
        return "", f"no se pudo lanzar ssh: {e}"
    salida, err, excedido = [], [], threading.Event()
    hilos = [threading.Thread(target=_lee_con_tope, args=(p.stdout, TOPE_BYTES, salida, excedido), daemon=True),
             threading.Thread(target=_lee_con_tope, args=(p.stderr, TOPE_ERR, err, threading.Event()), daemon=True)]
    for h in hilos:
        h.start()
    limite = time.time() + TOPE_S
    while p.poll() is None and time.time() < limite and not excedido.is_set():
        time.sleep(0.05)
    if p.poll() is None:
        _mata(p)
        p.wait()
        for h in hilos:
            h.join(2)
        if not excedido.is_set():
            return "", "timeout"
    else:
        for h in hilos:
            h.join(2)
    texto = b"".join(salida).decode("utf-8", "replace")
    if excedido.is_set():
        # se corta en el tope y se deja constancia: lo que ya llego sirve, pero las secciones con marco sin cierre no
        return texto + "\n### cortada\n", ""
    if p.returncode != 0 and "### version" not in texto:
        return "", clasifica_error(p.returncode, b"".join(err).decode("utf-8", "replace"))
    return texto, ""


def analizar(texto):
    """(secciones, marcadas, invalidas, completa) de la salida del servidor.

    Solo las secciones conocidas; el resto se ignora. Una sección que aparece dos veces es inválida (ni la primera ni la segunda valen: así un
    texto del servidor con un `### nombre` dentro no puede reabrir y vaciar una sección). Las secciones con marco (`MARCADAS`) solo valen con
    `n <lineas>` y `fin <lineas> <0>` coherentes entre sí y con lo recibido; se devuelven sin el marco. `completa` = el script llegó a `### fin`."""
    out, vistas, invalidas = {}, set(), set()
    actual, fin_visto, cortada, tras_fin = None, False, False, False
    # solo «\n» separa líneas: str.splitlines() corta también en U+0085, U+2028, \x1c-\x1e..., que {{json}} de Docker no escapa, y un nombre o una
    # etiqueta hostil reabriría secciones en el Mac sin que el servidor emitiera ningún salto de línea
    for linea in (texto or "").split("\n"):
        if linea.startswith("### "):
            nombre = linea[4:].strip()
            if nombre == "fin":
                fin_visto, actual = True, None
                continue
            tras_fin = tras_fin or fin_visto
            if nombre == "cortada":
                cortada = True
                if actual:                                # la sección abierta acaba en una línea partida: no vale
                    out.pop(actual, None)
                    invalidas.add(actual)
                actual = None
            elif nombre not in SECCIONES:
                actual = None
            elif nombre in vistas:
                invalidas.add(nombre)
                out.pop(nombre, None)
                actual = None
            else:
                vistas.add(nombre)
                out[nombre] = []
                actual = nombre
        elif actual:
            out[actual].append(linea.rstrip())
    for cuerpo in out.values():                    # split("\n") deja una línea vacía final cuando el texto termina en salto de línea
        while cuerpo and not cuerpo[-1]:
            cuerpo.pop()
    if "version_root" in out:
        # recoger-root v2 declara en «fin_root» si se saltó o agotó el tiempo de algún comando; entonces sus secciones no valen
        # (vacías no son «0 bloqueadas»: son «sin dato») y la lectura sigue como si no hubiera root
        if out.get("fin_root") != ["omitidos 0"]:
            for nombre in SECCIONES_ROOT:
                out.pop(nombre, None)
            invalidas.add("root")
    marcadas = set()
    for nombre in MARCADAS:
        cuerpo = out.get(nombre)
        if cuerpo is None:
            continue
        while cuerpo and not cuerpo[-1]:
            cuerpo.pop()
        if not cuerpo:
            continue
        esperado = _MARCO_N.fullmatch(cuerpo[0])
        if esperado is None:
            if cuerpo[0].startswith("n "):            # intenta ser v2 y no lo es (v1 nunca empieza por "n")
                out.pop(nombre)
                invalidas.add(nombre)
            continue                                  # sin marco: formato de v1
        interior = cuerpo[1:-1]
        cierre = _MARCO_FIN.fullmatch(cuerpo[-1]) if len(cuerpo) >= 2 else None
        if cierre and cierre[2] == "0" and int(esperado[1]) == int(cierre[1]) == len(interior):
            out[nombre] = interior
            marcadas.add(nombre)
        else:
            out.pop(nombre)
            invalidas.add(nombre)
    return out, marcadas, invalidas, fin_visto and not cortada and not tras_fin


def secciones(texto):
    """'### nombre' -> lineas (ver `analizar`)."""
    return analizar(texto)[0]


def _num(x, defecto=0.0):
    """Numero finito o el defecto (float() acepta 'nan' e 'inf', que luego rompen int())."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return defecto
    return v if math.isfinite(v) else defecto


def _clave_valor(lineas):
    d = {}
    for l in lineas:
        k, _, v = l.partition(" ")
        if k:
            d[k] = v.strip()
    return d


_IP = re.compile(r"^[0-9a-fA-F.:]{3,45}$")


def interpretar(sec, marcadas=frozenset(), ahora=None):
    """Secciones -> datos del servidor (numeros y listas cortas, listos para pintar). `marcadas` = secciones con marco validado (v2)."""
    d = {}
    s = sec.get("sistema") or []
    if len(s) >= 2:
        carga = _num(s[0].split()[0]) if s[0].split() else 0.0
        nucleos = int(_num(s[1], 1)) if s[1].strip().isdecimal() else 0
        if 0 < nucleos <= 1024:                     # si nproc no salio, la linea es otra cosa
            d["cpu"] = {"pct": round(min(100.0, 100.0 * carga / nucleos), 1), "carga": carga, "nucleos": nucleos}
    if len(s) >= 3 and "cpu" in d:
        d["uptime_s"] = int(_num(s[2]))
    m = (sec.get("memoria") or [""])[0].split()
    if len(m) == 2:
        total, disponible = _num(m[0]), _num(m[1])
        d["memoria"] = {"total": int(total), "usada": int(max(0, total - disponible))}
    k = (sec.get("disco") or [""])[0].split()
    if len(k) == 2:
        d["disco"] = {"total": int(_num(k[0])), "usado": int(_num(k[1]))}
    if "sesiones" in sec:
        ses = []
        for l in sec["sesiones"]:
            partes = l.split()
            if not partes:
                continue
            ip = re.search(r"\(([^)]*)\)", l)
            ses.append({"usuario": partes[0][:32], "desde": (ip.group(1) if ip else "")[:64]})
        d["sesiones"] = ses
    if "puertos" in sec:
        d["puertos"] = sorted({int(p) for p in " ".join(sec["puertos"]).split()
                               if p.isascii() and p.isdecimal() and 0 < int(p) < 65536})
    if "intentos" in sec:
        it = servidores_intentos.interpretar(sec["intentos"], "intentos" in marcadas, ahora)
        if it is not None:
            d["intentos"] = it
        elif "intentos" in marcadas:
            d["intentos_sin_dato"] = True               # el servidor no tiene fuente de intentos: se muestra «sin dato», nunca 0
    a = sec.get("actualizaciones") or []
    if a:
        d["actualizaciones"] = {"pendientes": int(_num(a[0])), "reinicio": len(a) > 1 and a[1] == "reinicio"}
        # «### espera» (si al script le sobró tiempo): lo que `apt upgrade` instalaría de verdad. El resto está en espera
        # (actualizaciones por fases de Ubuntu, paquetes retenidos): no son pendientes. Nunca baja de los parches de seguridad: un
        # parche retenido (p. ej. un kernel nuevo) sigue siendo pendiente
        e = (sec.get("espera") or [""])[0].split()
        if len(e) == 2 and e[0] == "instalables" and e[1].isdecimal() and len(e[1]) <= 9:
            todos = d["actualizaciones"]["pendientes"]
            seguridad = _num(_clave_valor(sec.get("bastionado") or []).get("parches_seguridad"))
            d["actualizaciones"]["pendientes"] = min(todos, max(int(e[1]), int(seguridad)))
            d["actualizaciones"]["en_espera"] = todos - d["actualizaciones"]["pendientes"]
    if "servicios" in sec or "contenedores" in sec:
        d["servicios"] = _servicios(sec.get("servicios") or [], sec.get("contenedores") or [])
    correo, puertos_correo = _correo(sec.get("servicios") or [], sec.get("contenedores") or [], sec.get("correo") or [])
    if correo:
        d["correo"], d["correo_puertos"] = correo, puertos_correo
    if "servicios" in sec:
        # nombres de los servicios de Swarm (con su estado): la identidad de un servicio solo vale si aparece aquí
        d["swarm_servicios"] = {n: ok for n, ok in ((s_["nombre"], s_["ok"]) for s_ in _servicios(sec["servicios"], [])) if servidores_inventario.NOMBRE.fullmatch(n)}
    if "inventario" in sec:
        inv = servidores_inventario.interpretar(sec["inventario"], "inventario" in marcadas)
        if inv is not None:
            d["inventario"] = inv
    if "webs" in sec:
        det = servidores_webs.interpretar(sec["webs"], "webs" in marcadas)
        if det is not None:
            d["webs_detectadas"] = det
    if "fail2ban" in sec:
        f = _clave_valor(sec["fail2ban"])
        d["fail2ban"] = ({"instalado": False} if "no-instalado" in sec["fail2ban"] else
                         {"instalado": True, "ahora": int(_num(f.get("ahora"))), "total": int(_num(f.get("total")))})
    if "bastionado" in sec or "bastionado_root" in sec:
        b = _clave_valor(sec.get("bastionado") or [])
        b.update(_clave_valor(sec.get("bastionado_root") or []))
        d["bastionado"] = {k[:40]: v[:40] for k, v in b.items()}
    d["con_root"] = "bastionado_root" in sec
    return d


def _servicios(servicios, contenedores):
    """Servicios de Swarm (nombre, replicas 'n/m') o, si no hay, contenedores (nombre, estado)."""
    out = []
    for l in servicios:
        nombre, _, rep = l.partition("\t")
        r = re.match(r"(\d+)/(\d+)", rep.strip())
        if nombre and r:
            # n/n bien; "(1/1 completed)" es una tarea terminada. 0/0 es PARADO: lo que dejan Dokploy y Easypanel al pulsar
            # «stop» (escalan a cero). No está caído (ok), pero no es verde: se marca aparte para que se vea
            ok = r.group(1) == r.group(2) or "completed" in rep
            out.append({"nombre": nombre[:64], "ok": ok, "detalle": rep.strip()[:16],
                        "parado": r.group(2) == "0" and "completed" not in rep})
    if out:
        return out
    for l in contenedores:
        partes = l.split("\t")
        if len(partes) >= 2 and partes[0]:
            # un contenedor que termino bien (migraciones, builds) no esta caido
            termino_bien = partes[1] == "exited" and len(partes) > 2 and partes[2].startswith("Exited (0)")
            out.append({"nombre": partes[0][:64], "ok": partes[1] == "running" or termino_bien,
                        "detalle": partes[1][:16]})
    return out


# servidores de correo conocidos y LOS PUERTOS QUE SIRVE cada uno: solo esos se aceptan (Postfix no sirve IMAP, así que con Postfix un 993
# abierto sigue siendo un aviso). Se compara PALABRA A PALABRA (el nombre partido por guiones, puntos, barras...), nunca como trozo de
# texto: «correo-mailu-front-1» o «mailcow_postfix» sí, «emailupdater» o «postfixadmin-web» no
_SMTP, _IMAP = (25, 465, 587), (993, 995)
_CORREO = {"mailu": ("Mailu", _SMTP + _IMAP), "mailcow": ("mailcow", _SMTP + _IMAP), "mailserver": ("docker-mailserver", _SMTP + _IMAP),
           "poste": ("Poste.io", _SMTP + _IMAP), "stalwart": ("Stalwart", _SMTP + _IMAP), "iredmail": ("iRedMail", _SMTP + _IMAP),
           "mailinabox": ("Mail-in-a-Box", _SMTP + _IMAP), "postfix": ("Postfix", _SMTP), "exim": ("Exim", _SMTP), "exim4": ("Exim", _SMTP),
           "opensmtpd": ("OpenSMTPD", _SMTP), "dovecot": ("Dovecot", _IMAP)}


def _correo(servicios, contenedores, sistema):
    """(nombre, puertos) de los servidores de correo EN MARCHA, o ("", []) si no hay: con ellos, esos puertos son legítimos."""
    en_marcha = [l.split("\t")[0] for l in servicios if re.match(r"(\d+)/\1(\s|$)", (l.split("\t") + [""])[1].strip()) and "\t0/0" not in l]
    en_marcha += [p[0] for p in (l.split("\t") for l in contenedores) if len(p) >= 2 and p[1] == "running"]
    en_marcha += [l.strip() for l in sistema]
    nombres, puertos = [], set()
    for nombre in en_marcha:
        for palabra in re.split(r"[^a-z0-9]+", nombre.lower()):
            if palabra in _CORREO:
                n, ps = _CORREO[palabra]
                if n not in nombres:
                    nombres.append(n)
                puertos.update(ps)
    return " + ".join(nombres[:3]), sorted(puertos)


def leer(servidor):
    """Conecta, lee e interpreta. Devuelve (datos, error, segundos)."""
    t0 = time.time()
    if not os.path.exists(CLAVE):
        return None, "falta la clave ~/.ssh/infobar_ed25519", 0.0     # sin intentos contra fail2ban
    salida, error = ejecutar(servidor)
    if error:
        return None, error, round(time.time() - t0, 2)
    try:
        sec, marcadas, invalidas, completa = analizar(salida)
        datos = interpretar(sec, marcadas)
        datos["completa"] = completa
        if invalidas:
            datos["secciones_invalidas"] = sorted(invalidas)
        return datos, "", round(time.time() - t0, 2)
    except Exception as e:                          # una salida inesperada no tumba el ciclo
        return None, f"salida no reconocida ({type(e).__name__})", round(time.time() - t0, 2)

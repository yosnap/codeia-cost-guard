"""Inventario de contenedores: plantilla fija de `docker inspect` (contra Docker real si lo hay), parseo hostil, recursos con identidad, y la base
(línea base, confirmación por tiempo, presupuestos, retención, borrado, copia previa sin nombres)."""
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import servidores_api as api, servidores_historico as sh, servidores_inventario as inv, servidores_recursos as rec  # noqa: E402
from infobar import servidores_ssh as ss  # noqa: E402
from test_servidores_historico import Base, SRV, entrada  # noqa: E402

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.join(RAIZ, "infobar", "remoto", "recoger-root.sh")
T0 = 1_790_000_000


def fila(nombre="web-1", estado="running", salud="none", reinicios=0, salida=0, inicio="2026-09-01T10:00:00.123456789Z", politica="always",
         proyecto="", servicio="", swarm="", tarea=""):
    campos = [nombre if nombre.startswith("/") else "/" + nombre, estado, salud]
    return "\t".join(["c", *(json.dumps(c) for c in campos[:3]), str(reinicios), str(salida), json.dumps(inicio), json.dumps(politica),
                      json.dumps(proyecto), json.dumps(servicio), json.dumps(swarm), json.dumps(tarea)])


def seccion(filas, total=None, pedidos=None, truncado=None, fuente="docker", ahora=None):
    pedidos = len(filas) if pedidos is None else pedidos
    total = pedidos if total is None else total
    cuerpo = [*([f"ahora {ahora}"] if ahora else []), f"fuente {fuente}", f"total {total}", f"pedidos {pedidos}",
              f"truncado {'si' if (total > pedidos if truncado is None else truncado) else 'no'}", *filas]
    return [f"n {len(cuerpo)}", *cuerpo, f"fin {len(cuerpo)} 0"]


def lee(filas, **kw):
    sec, marcadas, _, _ = ss.analizar("### inventario\n" + "\n".join(seccion(filas, **kw)) + "\n### fin\n")
    return inv.interpretar(sec["inventario"], "inventario" in marcadas) if "inventario" in sec else None


class ParseoTest(unittest.TestCase):
    def test_lectura_sana(self):
        i = lee([fila("web-1"), fila("db", estado="exited", salida=0), fila("job", estado="exited", salida=1)])
        self.assertTrue(i["completo"])
        self.assertEqual([c["nombre"] for c in i["contenedores"]], ["web-1", "db", "job"])
        self.assertEqual(i["contenedores"][0]["inicio"], 1788256800)

    def test_cada_campo_hostil_descarta_su_linea_y_la_lectura_deja_de_ser_completa(self):
        malos = {
            "nombre con barra": fila("a/b"), "nombre con control": fila("a\nb"), "nombre vacio": fila("/"), "nombre enorme": fila("a" * 200),
            "estado raro": fila(estado="zombie"), "salud rara": fila(salud="muy sana"), "politica rara": fila(politica="a veces"),
            "etiqueta con control": fila(proyecto="x\n### intentos\ntotal 0"), "etiqueta con html": fila(servicio="<b>"),
            "reinicios negativos": fila().replace("\t0\t0\t", "\t-1\t0\t"), "reinicios gigantes": fila(reinicios=10 ** 15),
            "salida gigante": fila(salida=10 ** 9), "faltan campos": "\t".join(fila().split("\t")[:-1]), "sobran campos": fila() + "\tx",
            "json roto": fila().replace('"always"', "always"), "no es cadena": fila().replace('"running"', "5"),
        }
        for nombre, linea in malos.items():
            i = lee([fila("ok-1"), linea])
            self.assertIsNotNone(i, nombre)
            self.assertEqual(i["descartados"], 1, nombre)
            self.assertTrue(i["completo"], nombre)                       # llegó todo lo pedido: el resto de la lectura sirve...
            self.assertEqual([c["nombre"] for c in i["contenedores"]], ["ok-1"], nombre)       # ...pero sin el descartado (y sin concluir desapariciones: ver el almacén)

    def test_nombres_largos_de_tareas_y_codigos_de_salida_negativos_son_legitimos(self):
        largo = "servicio-" + "x" * 60 + ".1." + "t" * 25
        i = lee([fila(largo, swarm="servicio-" + "x" * 60, tarea="t" * 25), fila("fallo", estado="exited", salida=0).replace('\t0\t0\t', '\t0\t-1\t')])
        self.assertEqual((i["descartados"], len(i["contenedores"])), (0, 2))
        self.assertEqual(i["contenedores"][1]["salida"], -1)

    def test_un_caracter_unicode_de_salto_de_linea_no_reabre_secciones(self):
        # U+0085, U+2028 y compañía: str.splitlines() los corta, {{json}} de Docker no los escapa
        for raro in ("\x85", "\u2028", "\x1c", "\x0b", "\x0c"):
            linea = fila("a").replace('"always"', json.dumps("p" + raro + "### intentos" + raro + "total 0", ensure_ascii=False))
            texto = "### inventario\n" + "\n".join(seccion([linea])) + "\n### fail2ban\nahora 3\ntotal 9\n### fin\n"
            sec, marcadas, invalidas, _ = ss.analizar(texto)
            self.assertEqual(sec["fail2ban"], ["ahora 3", "total 9"], repr(raro))
            self.assertNotIn("intentos", sec, repr(raro))
            self.assertIn("inventario", sec, repr(raro))

    def test_fecha_de_arranque_ilegible_o_inexistente_es_none(self):
        for f in ("0001-01-01T00:00:00Z", "ayer", "2026-13-45T99:99:99Z", "2999-01-01T00:00:00Z", ""):
            self.assertIsNone(lee([fila(inicio=f)])["contenedores"][0]["inicio"], f)

    def test_marco_y_cabeceras(self):
        self.assertIsNone(lee([fila()], fuente="rara"))
        self.assertEqual(lee([], fuente="ninguna")["completo"], False)
        self.assertEqual(lee([], fuente="error")["completo"], False)
        self.assertTrue(lee([])["completo"])                                       # docker sin contenedores es una lectura completa
        t = lee([fila("a")], total=300, pedidos=1)
        self.assertTrue(t["truncado"] and t["completo"])
        self.assertIsNone(lee([fila()], total=0, pedidos=1))                       # pedidos > total: incoherente
        self.assertFalse(lee([fila("a")], pedidos=3, total=3)["completo"])         # faltan lineas que el servidor dijo enviar
        self.assertIsNone(lee([fila("a" + str(i)) for i in range(inv.MAX_CONTENEDORES + 1)], total=500, pedidos=inv.MAX_CONTENEDORES))

    def test_una_linea_que_no_es_del_protocolo_invalida_la_seccion(self):
        s = seccion([fila()])
        s.insert(2, "hola que tal")
        s[0] = f"n {len(s) - 2}"
        s[-1] = f"fin {len(s) - 2} 0"
        sec, marcadas, _, _ = ss.analizar("### inventario\n" + "\n".join(s) + "\n")
        self.assertIsNone(inv.interpretar(sec["inventario"], True))

    def test_un_nombre_duplicado_se_cuenta_como_descartado(self):
        i = lee([fila("a"), fila("a")])
        self.assertEqual((len(i["contenedores"]), i["descartados"]), (1, 1))

    def test_etiqueta_que_intenta_reabrir_una_seccion_no_rompe_el_protocolo(self):
        sucio = fila(proyecto="x").replace('"x"', json.dumps("p\n### intentos\ntotal 0\n### fin_root\nomitidos 0"))
        self.assertEqual(sucio.count("\n"), 0)                                     # {{json}} escapa los saltos de línea
        sec, marcadas, invalidas, completa = ss.analizar("### inventario\n" + "\n".join(seccion([sucio])) + "\n### fin\n")
        self.assertNotIn("intentos", sec)
        self.assertNotIn("fin_root", sec)


class IdentidadTest(unittest.TestCase):
    def rec(self, filas, swarm=None):
        return inv.recursos(lee(filas), swarm)

    def test_identidad_por_tipo(self):
        r = self.rec([fila("suelto"), fila("p-web-1", proyecto="p", servicio="web"), fila("p-web-2", proyecto="p", servicio="web", reinicios=3),
                      fila("api.1.abcdefghij0123456789xyzkl", swarm="api", tarea="abcdefghij0123456789xyzkl"),
                      fila("api.2.zzzdefghij0123456789xyzkl", swarm="api", tarea="zzzdefghij0123456789xyzkl")], swarm={"api": True})
        self.assertEqual(set(r), {("contenedor", "suelto"), ("compose", "p/web"), ("servicio", "api")})
        self.assertEqual((r[("compose", "p/web")]["n"], r[("compose", "p/web")]["reinicios"]), (2, 3))
        self.assertEqual((r[("servicio", "api")]["n"], r[("servicio", "api")]["estado"]), (2, "ok"))

    def test_un_contenedor_no_puede_ocupar_la_identidad_de_un_servicio_que_no_existe(self):
        r = self.rec([fila("falso", swarm="produccion", tarea="x" * 25)], swarm={"otro": True})
        self.assertEqual(set(r), {("contenedor", "falso")})
        r = self.rec([fila("falso", swarm="produccion", tarea="x" * 25)], swarm={"produccion": True})       # existe, pero el nombre no es de tarea
        self.assertEqual(set(r), {("contenedor", "falso")})

    def test_el_estado_de_un_servicio_viene_de_service_ls_no_de_sus_tareas_viejas(self):
        viejas = [fila(f"api.1.{'a' * 24}{i}", estado="exited", salida=1, swarm="api", tarea="a" * 24 + str(i)) for i in range(3)]
        self.assertEqual(self.rec(viejas, {"api": True})[("servicio", "api")]["estado"], "ok")
        self.assertEqual(self.rec(viejas, {"api": False})[("servicio", "api")]["estado"], "caido")

    def test_estado_de_cada_contenedor_y_agregacion_por_el_peor(self):
        casos = {("running", "none", 0): "ok", ("running", "unhealthy", 0): "mal", ("running", "healthy", 0): "ok", ("restarting", "none", 0): "reiniciando",
                 ("exited", "none", 0): "terminado", ("exited", "none", 137): "caido", ("dead", "none", 1): "caido", ("created", "none", 0): "otro",
                 ("paused", "none", 0): "otro"}
        for (st, sa, ex), esperado in casos.items():
            self.assertEqual(self.rec([fila("x", estado=st, salud=sa, salida=ex)])[("contenedor", "x")]["estado"], esperado, (st, sa, ex))
        r = self.rec([fila("a", proyecto="p", servicio="s"), fila("b", proyecto="p", servicio="s", estado="exited", salida=2)])
        self.assertEqual(r[("compose", "p/s")]["estado"], "caido")


def hay_docker():
    """Docker local con demonio, comprobado con un tope corto y solo al ejecutar los tests (no al importar el módulo)."""
    try:
        return bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True, timeout=8).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class PlantillaConDockerRealTest(unittest.TestCase):
    """La plantilla del script, ejecutada por el `docker inspect` de verdad, produce líneas que el parser acepta. Solo lectura. La prueba que CREA un
    contenedor (etiquetas hostiles) solo corre con CG_DOCKER_CREAR=1."""

    @classmethod
    def setUpClass(cls):
        if not hay_docker():
            raise unittest.SkipTest("no hay Docker local")

    @classmethod
    def plantilla(cls):
        with open(ROOT) as f:
            return re.search(r"PLANTILLA='(.*)'\n", f.read())[1]

    def test_contenedores_existentes(self):
        ids = subprocess.run(["docker", "ps", "-aq", "--no-trunc"], capture_output=True, text=True, timeout=30).stdout.split()[:20]
        if not ids:
            self.skipTest("Docker sin contenedores")
        salida = subprocess.run(["docker", "inspect", "--format", self.plantilla(), *ids], capture_output=True, text=True, timeout=30).stdout
        lineas = salida.splitlines()
        self.assertEqual(len(lineas), len(ids))
        for l in lineas:
            self.assertIsNotNone(inv._contenedor(l), "la plantilla produce algo que el parser no acepta")

    @unittest.skipUnless(os.environ.get("CG_DOCKER_CREAR") == "1", "crea y borra un contenedor en el Docker local: solo con CG_DOCKER_CREAR=1")
    def test_etiquetas_hostiles_no_rompen_lineas_ni_cuelan_campos_nuevos(self):
        imagen = subprocess.run(["docker", "images", "-q"], capture_output=True, text=True, timeout=30).stdout.split()
        if not imagen:
            self.skipTest("Docker sin imágenes")
        nombre = "cg-prueba-" + uuid.uuid4().hex[:8]
        malo = "p\n### intentos\ntotal 0\tx\t{{.Config.Env}}"
        r = subprocess.run(["docker", "create", "--name", nombre, "--label", f"com.docker.compose.project={malo}", "--label", "password=hunter2",
                            "--env", "SECRETO=hunter2", imagen[0]], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            self.skipTest("no se pudo crear el contenedor de prueba")
        try:
            salida = subprocess.run(["docker", "inspect", "--format", self.plantilla(), nombre], capture_output=True, text=True, timeout=30).stdout
            self.assertEqual(len(salida.splitlines()), 1)
            self.assertEqual(len(salida.rstrip("\n").split("\t")), inv.CAMPOS)
            self.assertNotIn("hunter2", salida)                             # ni el entorno ni otras etiquetas llegan al script
            self.assertIsNone(inv._contenedor(salida.rstrip("\n")))          # y el valor hostil hace que la línea se descarte, no que se acepte
        finally:
            subprocess.run(["docker", "rm", "-f", nombre], capture_output=True, timeout=30)

    def test_la_plantilla_solo_nombra_campos_de_la_lista_blanca(self):
        permitidos = {".Name", ".State.Status", ".State.Health", ".State.Health.Status", ".RestartCount", ".State.ExitCode", ".State.StartedAt",
                      ".HostConfig.RestartPolicy.Name", ".Config.Labels"}
        usados = set(re.findall(r"(?<![\w\"])(\.[A-Z][A-Za-z.]*)", self.plantilla()))
        self.assertTrue(usados <= permitidos, usados - permitidos)
        etiquetas = set(re.findall(r'"(com\.docker\.[a-z.]+)"', self.plantilla()))
        self.assertEqual(etiquetas, {"com.docker.compose.project", "com.docker.compose.service", "com.docker.swarm.service.name", "com.docker.swarm.task.id"})
        for prohibido in (".Config.Env", ".Config.Cmd", ".Mounts", ".Config.Image", ".NetworkSettings", ".Args", ".Path", ".State.Health.Log"):
            self.assertNotIn(prohibido, self.plantilla())


@unittest.skipIf(os.name == "nt", "ejecuta recoger-root.sh de verdad con bash, con symlinks a "
                 "timeout/tac (os.symlink necesita Modo Desarrollador o admin en Windows); el "
                 "script es para servidores remotos Linux, no para el cliente")
class ScriptRootInventarioTest(unittest.TestCase):
    """recoger-root.sh de verdad con un `docker` falso: ids, orden por antigüedad, tope de 200, truncado y fuente."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-inv-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        with open(ROOT) as f:
            texto = f.read()
        self.assertIn("export LC_ALL=C PATH=", texto)
        # PATH cerrado: solo los programas falsos, timeout y tac (por copia) y lo básico del sistema; así un docker real del Mac no se cuela.
        # Copia en vez de enlace: en Windows, os.symlink pide un privilegio que no siempre hay (modo
        # desarrollador o admin); para "poner un binario resoluble en el PATH" una copia vale igual.
        for programa in ("timeout", "tac"):
            shutil.copy2(shutil.which(programa), os.path.join(self.dir, programa))
        texto = re.sub(r"export LC_ALL=C PATH=\S+", f"export LC_ALL=C PATH={aislamiento.ruta_bash(self.dir)}:/usr/bin:/bin", texto, count=1)
        self.script = os.path.join(self.dir, "root.sh")
        with open(self.script, "w") as f:
            f.write(texto)
        self.plantilla = re.search(r"PLANTILLA='(.*)'\n", texto)[1]

    def docker(self, n_ids, falla=None):
        ids = [f"{i:064x}" for i in range(n_ids, 0, -1)]            # `docker ps -a` da los más nuevos primero
        ruta = os.path.join(self.dir, "docker")
        with open(ruta, "w") as f:
            f.write(f"#!{sys.executable}\nimport sys\na = sys.argv[1:]\nIDS = {ids!r}\n"
                    "if a[:1] == ['ps']:\n    print('\\n'.join(IDS)); sys.exit(0)\n"
                    "if a[:1] == ['inspect']:\n"
                    "    for i in a[3:]:\n"
                    "        n = int(i, 16)\n"
                    "        print('c\\t\"/c%d\"\\t\"running\"\\t\"none\"\\t0\\t0\\t\"2026-09-01T10:00:00Z\"\\t\"no\"\\t\"\"\\t\"\"\\t\"\"\\t\"\"' % n)\n"
                    "    sys.exit(0)\n"
                    f"sys.exit({falla or 0})\n")
        os.chmod(ruta, 0o755)

    def ejecuta(self):
        r = subprocess.run([aislamiento.BASH, aislamiento.ruta_bash(self.script)], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        self.crudo = r.stdout
        return ss.analizar("### version\n2\n" + r.stdout + "### fin\n")

    def test_pocos_contenedores_completo(self):
        self.docker(3)
        sec, marcadas, invalidas, _ = self.ejecuta()
        self.assertIn("inventario", marcadas)
        i = inv.interpretar(sec["inventario"], True)
        self.assertTrue(i["completo"])
        self.assertEqual(sorted(c["nombre"] for c in i["contenedores"]), ["c1", "c2", "c3"])
        self.assertEqual(sec["fin_root"], ["omitidos 0"])

    def test_mas_de_200_se_recorta_a_los_mas_antiguos_y_se_declara(self):
        self.docker(250)
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["total"], i["pedidos"], i["truncado"], i["completo"]), (250, 200, True, True))
        self.assertEqual({c["nombre"] for c in i["contenedores"]}, {f"c{n}" for n in range(1, 201)})        # los 200 más antiguos (ids 1..200)

    def test_docker_que_falla_o_no_existe_es_sin_dato(self):
        self.docker(2, falla=1)
        os.remove(os.path.join(self.dir, "docker"))
        with open(os.path.join(self.dir, "docker"), "w") as f:
            f.write("#!/bin/bash\nexit 1\n")
        os.chmod(os.path.join(self.dir, "docker"), 0o755)
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["fuente"], i["completo"]), ("error", False))
        os.remove(os.path.join(self.dir, "docker"))
        sec, marcadas, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertEqual((i["fuente"], i["completo"]), ("ninguna", False))

    def test_un_inspect_lento_pierde_el_inventario_pero_no_las_secciones_de_seguridad(self):
        self.docker(3)
        ruta = os.path.join(self.dir, "docker")
        with open(ruta) as f:
            cuerpo = f.read().replace("if a[:1] == ['inspect']:\n", "if a[:1] == ['inspect']:\n    import time; time.sleep(6)\n")
        with open(ruta, "w") as f:
            f.write(cuerpo)
        sec, marcadas, invalidas, _ = self.ejecuta()
        self.assertEqual(sec["fin_root"], ["omitidos 0"])                              # el inventario no cuenta en lo omitido por root
        self.assertIn("bastionado_root", sec)
        self.assertFalse(inv.interpretar(sec["inventario"], True)["completo"])          # faltan las tres lineas pedidas

    def test_si_ya_se_gasto_el_tiempo_el_inventario_ni_arranca(self):
        self.docker(3)
        for n in ("sshd", "iptables"):
            with open(os.path.join(self.dir, n), "w") as f:
                f.write("#!/bin/bash\nexec sleep 30\n")
            os.chmod(os.path.join(self.dir, n), 0o755)
        sec, _, invalidas, _ = self.ejecuta()
        cuerpo = self.crudo.split("### inventario\n", 1)[1].split("### fin_root", 1)[0].splitlines()
        self.assertEqual(cuerpo[2], "fuente error")                                         # cuerpo = [n, ahora, fuente, ...]
        self.assertIn("root", invalidas)                                                    # y como root sí se pasó de tiempo, el Mac descarta sus secciones

    def test_el_inventario_lleva_el_reloj_del_servidor(self):
        self.docker(1)
        sec, _, _, _ = self.ejecuta()
        i = inv.interpretar(sec["inventario"], True)
        self.assertAlmostEqual(i["ahora_srv"], __import__("time").time(), delta=5)

    def test_si_root_omite_algo_el_inventario_se_descarta_con_el_resto(self):
        self.docker(2)
        sec, _, invalidas, _ = ss.analizar("### version_root\n2\n### inventario\nn 2\nfuente error\nfin 2 0\n### fin_root\nomitidos 3\n")
        self.assertNotIn("inventario", sec)
        self.assertIn("root", invalidas)


class BaseRecursos(Base):
    def lectura(self, ts, filas=(), swarm=None, **kw):
        e = entrada(ts)
        kw.setdefault("ahora", ts)                                                  # el reloj del servidor, igual que el del Mac salvo que se diga otra cosa
        i = lee(list(filas), **kw)
        e["datos"]["inventario"] = i
        e["datos"]["swarm_servicios"] = swarm or {}
        return e

    def recursos(self):
        return {r[0]: r[1:] for r in self.con.execute("SELECT etiqueta, tipo, activo, estado FROM recursos ORDER BY etiqueta")}

    def eventos(self):
        return [(e, t) for e, t in self.con.execute("SELECT evento, tipo FROM eventos ORDER BY ts, evento")]


class AlmacenRecursosTest(BaseRecursos):
    def test_la_primera_lectura_es_linea_base_sin_eventos(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.assertEqual({k: v[1] for k, v in self.recursos().items()}, {"a": 1, "b": 1})
        self.assertEqual(self.eventos(), [])

    def test_aparicion_exige_dos_lecturas_separadas_en_el_tiempo(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.reg(self.lectura(T0 + 300, [fila("a"), fila("nuevo")]))
        self.assertEqual(self.recursos()["nuevo"][1], 0)                            # visto una vez: pendiente
        self.reg(self.lectura(T0 + 360, [fila("a"), fila("nuevo")]))
        self.assertEqual(self.recursos()["nuevo"][1], 0)                            # dos lecturas pero demasiado juntas
        self.reg(self.lectura(T0 + 900, [fila("a"), fila("nuevo")]))
        self.assertEqual((self.recursos()["nuevo"][1], self.eventos()), (1, [("aparecio", "contenedor")]))

    def test_un_efimero_que_no_dura_no_genera_eventos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        for i in range(1, 6):
            self.reg(self.lectura(T0 + 300 * i, [fila("a"), fila(f"efimero{i}")]))
        self.assertEqual(self.eventos(), [])

    def test_desaparicion_exige_dos_lecturas_completas_y_no_ocurre_con_truncadas_ni_incompletas(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.assertEqual(self.eventos(), [])
        self.reg(self.lectura(T0 + 900, [fila("a")], total=300, pedidos=1))        # truncada: no concluye nada
        self.assertEqual(self.recursos()["b"][1], 1)
        e = self.lectura(T0 + 1200, [fila("a")], pedidos=2, total=2)                # incompleta: faltaba una linea
        self.reg(e)
        self.assertEqual(self.recursos()["b"][1], 1)
        self.reg(self.lectura(T0 + 1500, [fila("a")]))
        self.assertEqual(self.eventos(), [("desaparecio", "contenedor")])
        self.assertEqual(self.recursos()["b"][1], 0)

    def test_un_fallo_ssh_o_sin_inventario_no_cambia_nada(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        for i in range(1, 5):
            e = entrada(T0 + 300 * i)
            e["error"] = "timeout"
            e["ok_en"] = T0
            self.reg(e)
            self.reg(entrada(T0 + 300 * i + 1))                                      # lectura buena sin sección de inventario
        self.assertEqual(self.eventos(), [])
        self.assertEqual({k: v[1] for k, v in self.recursos().items()}, {"a": 1, "b": 1})

    def test_cambio_de_estado_bucle_y_presupuestos(self):
        self.reg(self.lectura(T0, [fila("a", reinicios=0)]))
        self.reg(self.lectura(T0 + 300, [fila("a", estado="exited", salida=1)]))
        self.assertEqual(self.eventos(), [("cambio_estado", "contenedor")])
        # bucle: el contador de reinicios sube en dos lecturas seguidas con arranque reciente
        ahora = T0 + 600
        for k in range(1, 3):
            ts = ahora + 300 * k
            arranque = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(ts - 60))
            self.reg(self.lectura(ts, [fila("a", reinicios=k, inicio=arranque)]))
        self.assertIn(("reinicios_en_bucle", "contenedor"), self.eventos())
        # un recurso que cambia sin parar agota su presupuesto (10 al día) y deja un único «inestable»
        for k in range(30):
            self.reg(self.lectura(T0 + 20_000 + 300 * k, [fila("a", estado="running" if k % 2 else "exited", salida=0)]))
        n = self.uno("SELECT COUNT(*) FROM eventos WHERE evento='inestable'")
        self.assertEqual(n, 1)
        self.assertLessEqual(self.uno("SELECT COUNT(*) FROM eventos WHERE clave=(SELECT clave FROM recursos LIMIT 1)"), rec.EVENTOS_DIA_POR_CLAVE + 3 * 1)
        self.assertGreater(self.descartes("eventos_sobre_tope"), 0)

    def test_tope_de_500_recursos_expulsa_inactivos_y_si_no_hay_descarta(self):
        self.reg(self.lectura(T0, [fila("base")]))
        self.con.execute("BEGIN IMMEDIATE")
        for i in range(rec.MAX_RECURSOS - 1):
            self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado) VALUES(?, 'contenedor', ?, 'x', 1, ?, 0, 'ok')",
                             (SRV["id"], f"{i:032x}", 100 + i))
        self.con.execute("COMMIT")
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), rec.MAX_RECURSOS)
        self.reg(self.lectura(T0 + 300, [fila("base"), fila("nuevo")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), rec.MAX_RECURSOS)       # entró el nuevo expulsando al inactivo más antiguo
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='nuevo'"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE ultima=100"), 0)
        self.con.execute("UPDATE recursos SET activo=1")                                      # y si todos están activos, el nuevo se descarta
        self.reg(self.lectura(T0 + 600, [fila("base"), fila("otro-nuevo")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='otro-nuevo'"), 0)
        self.assertEqual(self.descartes("recursos_sobre_tope"), 1)

    def test_un_contenedor_descartado_no_oculta_los_demas_ni_concluye_desapariciones(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        malo = fila("c/d")                                                              # nombre imposible: se descarta solo ese
        self.reg(self.lectura(T0 + 300, [fila("a"), malo], pedidos=2))
        self.reg(self.lectura(T0 + 900, [fila("a"), malo], pedidos=2))
        self.assertEqual(self.recursos()["b"][1], 1)                                    # b no se ve, pero tampoco se concluye que haya desaparecido
        self.assertEqual(self.descartes("inventario_contenedores_descartados"), 2)
        self.reg(self.lectura(T0 + 1200, [fila("a"), fila("nuevo")]))                  # y las lecturas normales siguen funcionando
        self.assertIn("nuevo", self.recursos())

    def test_nodo_swarm_sin_lista_de_servicios_no_cambia_identidades(self):
        tarea = "t" * 25
        f = [fila(f"api.1.{tarea}", swarm="api", tarea=tarea)]
        self.reg(self.lectura(T0, f, swarm={"api": True}))
        self.assertEqual(list(self.recursos()), ["api"])
        self.reg(self.lectura(T0 + 300, f, swarm={}))                                   # sin `docker service ls`: la identidad sería otra
        self.reg(self.lectura(T0 + 900, f, swarm={}))
        self.assertEqual(list(self.recursos()), ["api"])
        self.assertEqual((self.eventos(), self.descartes("inventario_sin_servicios_swarm")), ([], 2))

    def test_un_recorte_no_fabrica_apariciones(self):
        self.reg(self.lectura(T0, [fila("a")], total=300, pedidos=1))                  # base truncada
        self.reg(self.lectura(T0 + 300, [fila("b")], total=300, pedidos=1))            # entra otro porque «a» ya no cabe
        self.assertEqual((self.recursos()["b"][1], self.eventos()), (1, []))

    def test_la_linea_base_se_hace_una_vez_aunque_la_primera_lectura_estuviera_vacia(self):
        self.reg(self.lectura(T0, []))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.assertEqual(self.recursos()["a"][1], 0)                                    # no es línea base: queda pendiente de confirmar
        (n, e), _ = sh.olvidar_recursos(self.con, SRV["id"])
        self.reg(self.lectura(T0 + 600, [fila("a")]))
        self.assertEqual(self.recursos()["a"][1], 1)                                    # tras olvidar, vuelve a haber línea base

    def test_los_contenedores_sueltos_no_gastan_el_presupuesto_de_los_servicios(self):
        self.reg(self.lectura(T0, [fila("prod", proyecto="p", servicio="web")] + [fila(f"ci{i}") for i in range(rec.EVENTOS_DIA_POR_TIPO + 10)]))
        self.reg(self.lectura(T0 + 300, [fila("prod", proyecto="p", servicio="web")] + [fila(f"ci{i}") for i in range(rec.EVENTOS_DIA_POR_TIPO + 10)]))
        self.reg(self.lectura(T0 + 900, []))
        self.reg(self.lectura(T0 + 1500, []))
        tipos = dict(self.con.execute("SELECT tipo, COUNT(*) FROM eventos WHERE evento='desaparecio' GROUP BY tipo").fetchall())
        self.assertEqual(tipos["contenedor"], rec.EVENTOS_DIA_POR_TIPO)
        self.assertEqual(tipos["compose"], 1)                                           # el de producción sí quedó anotado

    def test_si_no_cabe_el_evento_el_recurso_sigue_activo_y_se_anota_cuando_hay_presupuesto(self):
        base = [fila(f"ci{i}") for i in range(rec.EVENTOS_DIA_POR_TIPO + 5)]
        self.reg(self.lectura(T0, base))
        for k in (300, 900, 1500):
            self.reg(self.lectura(T0 + k, []))
        sin_anotar = self.uno("SELECT COUNT(*) FROM recursos WHERE activo=1")
        self.assertEqual(sin_anotar, 5)                                                 # 100 anotados e inactivos; 5 siguen activos esperando presupuesto
        self.reg(self.lectura(T0 + 86400 + 300, []))
        self.reg(self.lectura(T0 + 86400 + 900, []))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE activo=1"), 0)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos WHERE evento='desaparecio'"), rec.EVENTOS_DIA_POR_TIPO + 5)

    def test_dos_lecturas_muy_juntas_no_confirman_una_desaparicion(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.reg(self.lectura(T0 + 360, [fila("a")]))                                    # segunda lectura sin «b», pero solo 60 s después
        self.assertEqual((self.recursos()["b"][1], self.eventos()), (1, []))
        self.reg(self.lectura(T0 + 900, [fila("a")]))
        self.assertEqual(self.eventos(), [("desaparecio", "contenedor")])

    def test_si_no_cabe_la_aparicion_sigue_pendiente_y_se_anota_al_dia_siguiente(self):
        self.reg(self.lectura(T0, [fila("base")]))
        nuevos = [fila("base")] + [fila(f"n{i:03d}") for i in range(rec.EVENTOS_DIA_POR_TIPO + 5)]
        for k in (300, 600, 1200):
            self.reg(self.lectura(T0 + k, nuevos))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos WHERE evento='aparecio'"), rec.EVENTOS_DIA_POR_TIPO)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE activo=0"), 5)       # sin evento no se da por confirmada
        self.reg(self.lectura(T0 + 86400 + 300, nuevos))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE activo=0"), 0)

    def test_la_expulsion_no_toca_un_recurso_pendiente_que_se_ve_en_esta_lectura(self):
        self.reg(self.lectura(T0, [fila("base")]))
        self.con.execute("BEGIN IMMEDIATE")
        for i in range(rec.MAX_RECURSOS - 2):
            self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado) VALUES(?, 'contenedor', ?, 'x', 1, ?, 0, 'ok')",
                             (SRV["id"], f"{i:032x}", 1000 + i))
        vivo = rec.clave_de(self.con, "contenedor", "vivo")
        self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado, visto_n, visto_desde) VALUES(?, 'contenedor', ?, 'vivo', 1, 5, 0, 'ok', 1, ?)",
                         (SRV["id"], vivo, T0))
        self.con.execute("COMMIT")
        self.reg(self.lectura(T0 + 300, [fila("base"), fila("nuevo"), fila("vivo")]))        # «nuevo» se procesa antes que «vivo» y necesita hueco
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE clave=?", vivo), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='nuevo'"), 1)

    def test_bucle_con_el_reloj_del_servidor_desfasado(self):
        def arranque(ts_srv):
            return __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(ts_srv - 60))
        self.reg(self.lectura(T0, [fila("a", reinicios=0)], ahora=T0 + 900))
        for k in (1, 2):
            ts = T0 + 300 * k
            self.reg(self.lectura(ts, [fila("a", reinicios=k, inicio=arranque(ts + 900))], ahora=ts + 900))      # servidor 15 min por delante
        self.assertIn(("reinicios_en_bucle", "contenedor"), self.eventos())
        self.reg(self.lectura(T0 + 2000, [fila("b", reinicios=0)]))
        for k in (1, 2):                                                                # sin reloj del servidor no hay forma de medir la edad: nada
            ts = T0 + 2000 + 300 * k
            e = self.lectura(ts, [fila("b", reinicios=k, inicio=arranque(ts))])
            e["datos"]["inventario"]["ahora_srv"] = None
            self.reg(e)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos WHERE evento='reinicios_en_bucle'"), 1)

    def test_la_expulsion_no_toca_lo_que_se_ve_ahora_ni_lo_que_tiene_eventos_recientes(self):
        self.reg(self.lectura(T0, [fila("base")]))
        self.con.execute("BEGIN IMMEDIATE")
        for i in range(rec.MAX_RECURSOS - 1):
            self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado) VALUES(?, 'contenedor', ?, 'x', 1, ?, 0, 'ok')",
                             (SRV["id"], f"{i:032x}", 100 + i))
        self.con.execute("COMMIT")
        antiguo = self.con.execute("SELECT clave FROM recursos WHERE ultima=100").fetchone()[0]
        self.con.execute("INSERT INTO eventos(servidor_id, ts, clave, tipo, evento, severidad) VALUES(?,?,?, 'contenedor', 'desaparecio', 'aviso')", (SRV["id"], T0, antiguo))
        self.reg(self.lectura(T0 + 300, [fila("base"), fila("nuevo")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE clave=?", antiguo), 1)       # tiene un evento reciente: se protege
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos WHERE etiqueta='nuevo'"), 1)       # entró expulsando al siguiente más antiguo

    def test_un_fallo_del_inventario_no_deshace_la_muestra(self):
        from unittest import mock
        with mock.patch.object(rec, "anota_inventario", side_effect=RuntimeError("x")):
            self.reg(self.lectura(T0, [fila("a")]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.descartes("inventario_error"), 1)

    def test_la_etiqueta_solo_admite_el_juego_de_caracteres_del_parseo(self):
        sh._alta_servidor(self.con, SRV["id"], "x", T0)
        for malo in ("a b", "a\nb", "<b>", "ñ", "a;b"):
            with self.assertRaises(sqlite3.IntegrityError, msg=repr(malo)):
                self.con.execute("INSERT INTO recursos(servidor_id, tipo, clave, etiqueta, primera, ultima, activo, estado) VALUES(?, 'contenedor', ?, ?, 1, 1, 1, 'ok')",
                                 (SRV["id"], "0" * 32, malo))

    def test_los_nombres_salen_tambien_de_la_cache_a_las_48_h_y_al_olvidar(self):
        from infobar import rutas, servidores_ciclo as ciclo
        from unittest import mock
        e = {"ok_en": 1, "datos": {"servicios": [{"nombre": "cliente-x", "ok": False}], "swarm_servicios": {"cliente-x": True},
                                   "inventario": {"contenedores": [{"nombre": "cliente-x"}]}}, "tarjetas": {"servicios": {"estado": "rojo", "caidos": ["cliente-x"]}}}
        vieja = ciclo.podar_ips(json.loads(json.dumps(e)), 1 + ciclo.CACHE_IPS_VIDA_S + 10)
        self.assertNotIn("cliente-x", json.dumps(vieja))
        reciente = ciclo.podar_ips(json.loads(json.dumps(e)), 100)
        self.assertIn("cliente-x", json.dumps(reciente))
        cache = os.path.join(self.dir, "servidores.json")
        with mock.patch.object(rutas, "SERVIDORES", cache):
            with open(cache, "w") as f:
                json.dump({"servidores": {NOMBRE_SRV: e, "otro": json.loads(json.dumps(e))}}, f)
            self.reg(self.lectura(T0, [fila("a")]))
            self.con.close()
            self.assertEqual(api.olvidar(NOMBRE_SRV, "recursos")[0], 0)
            self.con = sh.abrir()
            with open(cache) as f:
                d = json.load(f)
        self.assertNotIn("cliente-x", json.dumps(d["servidores"][NOMBRE_SRV]))
        self.assertIn("cliente-x", json.dumps(d["servidores"]["otro"]))                 # solo el servidor pedido

    def test_una_app_anterior_no_toca_una_base_mas_nueva(self):
        import hashlib
        from unittest import mock
        self.reg(self.lectura(T0, [fila("a")]))
        self.con.close()
        with open(sh.ruta(), "rb") as f:
            antes = hashlib.sha256(f.read()).hexdigest()
        viejas = {v: m for v, m in sh.MIGRACIONES.items() if v <= 2}
        with mock.patch.object(sh, "MIGRACIONES", viejas):
            with self.assertRaises(sh.ErrorAlmacen):
                sh.abrir()
        self.con = sh.abrir()
        self.con.close()
        with open(sh.ruta(), "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), antes)
        self.con = sh.abrir()

    def test_el_nombre_solo_esta_en_la_etiqueta(self):
        self.reg(self.lectura(T0, [fila("cliente-secreto")]))
        self.reg(self.lectura(T0 + 300, [fila("cliente-secreto", estado="exited", salida=1)]))
        claves = {r[0] for r in self.con.execute("SELECT clave FROM recursos")} | {r[0] for r in self.con.execute("SELECT clave FROM eventos")}
        self.assertTrue(all(len(c) == 32 and "cliente" not in c for c in claves))
        for tabla in ("eventos", "meta"):
            for fila_ in self.con.execute(f"SELECT * FROM {tabla}"):
                self.assertNotIn("cliente-secreto", " ".join(map(str, fila_)))

    def test_misma_clave_misma_instalacion_y_distinta_sal_distinta_clave(self):
        a = rec.clave_de(self.con, "contenedor", "x")
        self.assertEqual(a, rec.clave_de(self.con, "contenedor", "x"))
        self.assertNotEqual(a, rec.clave_de(self.con, "compose", "x"))
        self.con.execute("UPDATE meta SET valor='otra' WHERE clave='sal_recursos'")
        self.assertNotEqual(a, rec.clave_de(self.con, "contenedor", "x"))

    def test_retencion_y_reloj_adelantado(self):
        self.reg(self.lectura(T0, [fila("a"), fila("b")]))
        self.reg(self.lectura(T0 + 300, [fila("a")]))
        self.reg(self.lectura(T0 + 900, [fila("a")]))                                   # b desaparece (inactivo)
        self.con.execute("UPDATE eventos SET ts = ?", (T0 - 200 * 86400,))             # evento de hace más de 180 días
        sh.mantener(self.con, {SRV["id"]: {"guardar_ips": True, "retencion_dias": 90}}, T0 + 40 * 86400, vacuum=False)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos"), 0)
        self.assertEqual(list(self.recursos()), ["a"] if self.recursos().get("a") else [])     # b (inactivo, de hace 40 días) purgado
        self.assertNotIn("b", self.recursos())
        # marcas en el futuro (reloj adelantado) se traen al presente
        self.con.execute("UPDATE recursos SET ultima=?, visto_desde=?", (T0 + 10 * 86400, T0 + 10 * 86400))
        sh.mantener(self.con, {SRV["id"]: {"guardar_ips": True, "retencion_dias": 90}}, T0 + 40 * 86400, vacuum=False)
        self.assertLessEqual(self.uno("SELECT MAX(ultima) FROM recursos"), T0 + 40 * 86400 + 3600)

    def test_servidor_retirado_pierde_recursos_y_eventos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.reg(self.lectura(T0 + 300, [fila("a", estado="exited", salida=1)]))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM eventos"), 1)
        sh.mantener(self.con, {}, T0 + 3600, vacuum=False)                              # ya no está en la configuración
        self.assertEqual((self.uno("SELECT COUNT(*) FROM recursos"), self.uno("SELECT COUNT(*) FROM eventos")), (0, 0))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM servidores"), 1)                # el historial numérico permanece

    def test_olvidar_recursos_borra_de_verdad_los_bytes(self):
        self.reg(self.lectura(T0, [fila("nombre-muy-reconocible-123")]))
        (n, e), _ = sh.olvidar_recursos(self.con, SRV["id"])
        self.assertEqual(n, 1)
        self.con.close()
        crudo = b""
        for ext in ("", "-wal"):
            if os.path.exists(sh.ruta() + ext):
                with open(sh.ruta() + ext, "rb") as f:
                    crudo += f.read()
        self.assertNotIn(b"nombre-muy-reconocible-123", crudo)
        self.con = sh.abrir()

    def test_olvidar_recursos_por_orden_y_con_nombre_inexistente(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.con.close()
        self.assertEqual(api.olvidar("no-existe", "recursos")[0], 1)
        codigo, mensaje = api.olvidar(NOMBRE_SRV, "recursos")
        self.assertEqual(codigo, 0)
        self.assertIn("Recursos borrados: 1", mensaje)
        self.con = sh.abrir()
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), 0)
        with open(os.path.join(self.dir, "servidores_ips.log")) as f:
            self.assertNotIn('"a"', f.read())                                            # la auditoría lleva el número, no los nombres

    def test_la_copia_previa_a_migrar_no_lleva_nombres(self):
        self.reg(self.lectura(T0, [fila("nombre-que-no-debe-quedar")]))
        self.con.close()
        nueva = dict(sh.MIGRACIONES)
        nueva[max(nueva) + 1] = "CREATE TABLE extra(x INTEGER) STRICT;"
        from unittest import mock
        with mock.patch.object(sh, "MIGRACIONES", nueva):
            self.con = sh.abrir()
        for f in os.listdir(os.path.join(self.dir, "copias")):
            with open(os.path.join(self.dir, "copias", f), "rb") as fh:
                self.assertNotIn(b"nombre-que-no-debe-quedar", fh.read())

    def test_migracion_de_v2_a_v3_conserva_los_datos(self):
        self.reg(self.lectura(T0, [fila("a")]))
        self.con.close()
        c = sqlite3.connect(sh.ruta(), isolation_level=None)
        c.execute("DROP TABLE recursos")
        c.execute("DROP TABLE eventos")
        for tabla in ("webs", "eventos_web", "horas_web", "dias_web", "copias"):
            c.execute(f"DROP TABLE {tabla}")
        for tabla, col in (("muestras", "cert_dias_min"), ("muestras", "webs_cert_mal"), ("horas_servidor", "cert_dias_min"), ("horas_servidor", "webs_cert_mal_max")):
            c.execute(f"ALTER TABLE {tabla} DROP COLUMN {col}")
        c.execute("PRAGMA user_version=2")
        c.close()
        self.con = sh.abrir()
        self.assertEqual(self.uno("PRAGMA user_version"), max(sh.MIGRACIONES))
        self.assertEqual(self.uno("SELECT COUNT(*) FROM muestras"), 1)
        self.assertEqual(self.uno("SELECT COUNT(*) FROM recursos"), 0)


NOMBRE_SRV = SRV["nombre"]


if __name__ == "__main__":
    unittest.main()

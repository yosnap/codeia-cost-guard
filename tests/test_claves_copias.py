"""Claves de las copias: generación con ensayo, privada solo en el destino elegido, rechazo de rutas peligrosas, permisos,
errata, frase y clave de firma del dispositivo."""
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import aislamiento  # noqa: E402,F401
from infobar import cgx, claves_copias, rutas  # noqa: E402

try:
    import cryptography  # noqa: F401
    HAY = True
except ImportError:
    HAY = False

FRASE = "una frase de prueba larga"


@unittest.skipUnless(HAY, "falta cryptography")
class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cg-claves-")
        self._datos = rutas.DATOS
        rutas.DATOS = os.path.join(self.dir, "datos")
        self.fuera = os.path.join(self.dir, "medio-extraible")
        os.makedirs(self.fuera)
        self.copias = os.path.join(self.dir, "copias")
        os.makedirs(self.copias)

    def tearDown(self):
        rutas.DATOS = self._datos
        shutil.rmtree(self.dir, ignore_errors=True)


class TestGenerar(Base):
    def test_genera_ensaya_y_no_deja_la_privada_en_datos(self):
        ruta = os.path.join(self.fuera, "clave.txt")
        r = claves_copias.generar(ruta, self.copias)
        self.assertTrue(r["publica"].startswith("cgx1"))
        if os.name != "nt":                    # NTFS no tiene bits de permiso POSIX
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
        priv, avisos = claves_copias.cargar_privada(ruta)
        self.assertEqual(cgx.codificar_clave("publica", cgx.publica_de(priv)), r["publica"])
        self.assertEqual(avisos, [])
        for raiz, _d, ficheros in os.walk(rutas.DATOS):
            for f in ficheros:
                with open(os.path.join(raiz, f), "rb") as h:
                    self.assertNotIn(b"CGX-SECRET-KEY", h.read())

    def test_con_frase_la_clave_va_cifrada(self):
        ruta = os.path.join(self.fuera, "clave.cgx")
        claves_copias.generar(ruta, self.copias, frase=FRASE)
        with open(ruta, "rb") as f:
            self.assertTrue(f.read().startswith(cgx.MAGIA))
        with self.assertRaises(cgx.ErrorCgx):
            claves_copias.cargar_privada(ruta)
        with self.assertRaises(cgx.ErrorCgx):
            claves_copias.cargar_privada(ruta, lambda: "otra frase distinta")
        priv, _ = claves_copias.cargar_privada(ruta, lambda: FRASE)
        self.assertEqual(len(priv), 32)

    def test_rutas_peligrosas(self):
        with self.assertRaisesRegex(cgx.ErrorCgx, "carpeta de las copias"):
            claves_copias.generar(os.path.join(self.copias, "k.txt"), self.copias)
        with self.assertRaisesRegex(cgx.ErrorCgx, "sincronizada"):
            claves_copias.generar(os.path.join(self.dir, "Dropbox", "k.txt"), self.copias)
        self.assertFalse(os.path.exists(os.path.join(self.copias, "k.txt")))

    def test_no_pisa_una_clave_existente(self):
        ruta = os.path.join(self.fuera, "clave.txt")
        claves_copias.generar(ruta, self.copias)
        with open(ruta, "rb") as f:
            antes = f.read()
        with self.assertRaisesRegex(cgx.ErrorCgx, "no se sobrescribe"):
            claves_copias.generar(ruta, self.copias)
        with open(ruta, "rb") as f:
            self.assertEqual(f.read(), antes)

    def test_frase_debil_no_genera_nada(self):
        with self.assertRaises(cgx.ErrorCgx):
            claves_copias.generar(os.path.join(self.fuera, "k.cgx"), self.copias, frase="corta")
        self.assertEqual(os.listdir(self.fuera), [])


class TestCargar(Base):
    # NOTA DE SEGURIDAD: _permisos_cerrados() (infobar/claves_copias.py) se salta esta comprobacion
    # en Windows a proposito (NTFS no tiene permisos POSIX): no es solo que el test no pueda
    # simularlo, la proteccion real esta ausente en esa plataforma.
    @unittest.skipIf(os.name == "nt", "NTFS no tiene bits de permiso POSIX; _permisos_cerrados() ya "
                     "se salta esta comprobacion en Windows (infobar/claves_copias.py)")
    def test_permisos_abiertos_se_rechazan(self):
        ruta = os.path.join(self.fuera, "clave.txt")
        claves_copias.generar(ruta, self.copias)
        os.chmod(ruta, 0o644)
        with self.assertRaisesRegex(cgx.ErrorCgx, "legible por otros"):
            claves_copias.cargar_privada(ruta)

    def test_errata_en_el_fichero(self):
        ruta = os.path.join(self.fuera, "clave.txt")
        claves_copias.generar(ruta, self.copias)
        with open(ruta) as f:
            texto = f.read()
        i = len("CGX-SECRET-KEY-1-") + 5
        with open(ruta, "w") as f:
            f.write(texto[:i] + ("a" if texto[i] != "a" else "b") + texto[i + 1:])
        with self.assertRaisesRegex(cgx.ErrorCgx, "mal escrita"):
            claves_copias.cargar_privada(ruta)

    def test_ensayo_detecta_otro_par(self):
        ruta = os.path.join(self.fuera, "clave.txt")
        claves_copias.generar(ruta, self.copias)
        _otra, otra_pub = cgx.generar_par()
        firma_priv, firma_pub = claves_copias.clave_de_firma()
        with self.assertRaisesRegex(cgx.ErrorCgx, "ensayo falló"):
            claves_copias.ensayo(otra_pub, ruta, None, firma_priv, firma_pub)

    def test_aviso_si_la_privada_esta_en_carpeta_sincronizada(self):
        ruta = os.path.join(self.dir, "Dropbox", "k.txt")
        os.makedirs(os.path.dirname(ruta))
        priv, _ = cgx.generar_par()
        with open(ruta, "w") as f:
            f.write(cgx.codificar_clave("privada", priv))
        os.chmod(ruta, 0o600)
        self.assertIn("sincronizada", " ".join(claves_copias.cargar_privada(ruta)[1]))


class TestFirmaDelDispositivo(Base):
    def test_se_crea_una_vez_y_es_estable_y_privada(self):
        a = claves_copias.clave_de_firma()
        b = claves_copias.clave_de_firma()
        self.assertEqual(a, b)
        if os.name != "nt":                    # NTFS no tiene bits de permiso POSIX
            self.assertEqual(stat.S_IMODE(os.stat(claves_copias.ruta_firma()).st_mode), 0o600)

    # mismo hueco de seguridad que TestCargar.test_permisos_abiertos_se_rechazan
    @unittest.skipIf(os.name == "nt", "NTFS no tiene bits de permiso POSIX; _permisos_cerrados() ya "
                     "se salta esta comprobacion en Windows (infobar/claves_copias.py)")
    def test_permisos_abiertos_se_rechazan(self):
        claves_copias.clave_de_firma()
        os.chmod(claves_copias.ruta_firma(), 0o644)
        with self.assertRaises(cgx.ErrorCgx):
            claves_copias.clave_de_firma()


if __name__ == "__main__":
    unittest.main()

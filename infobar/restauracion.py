"""Restaurar una copia o exportación `.cgx` sobre la base. Nada se toca hasta que el fichero está descifrado, autenticado y comprobado.

Orden: (1) pone el marcador de restauración (la barra y la ingesta lo respetan sin abrir la base y sueltan el puesto) y toma el puesto
de escritor con identidad propia, esperando a que el de la barra caduque; (2) descifra a un temporal privado; (3) comprueba
`user_version`, `quick_check` y que sea una base de consumo; (4) pide confirmación con lo que trae; (5) copia de seguridad en claro de
la base actual (`backup()`, nunca copiando ficheros), checkpoint, cierre y borrado de `-wal`/`-shm` — SQLite aplicaría el WAL de
la base anterior encima de la restaurada, y `quick_check` diría `ok`; si no se pueden borrar, se aborta sin tocar nada —; (6)
`os.replace` con reintentos CON EL PUESTO TODAVÍA TOMADO; (7) reabre con `almacen.abrir()` para comprobarla. Una base actual dañada
que no se puede abrir se aparta (con sus `-wal`/`-shm`) en la carpeta de copias previas en vez de bloquear la restauración.
"""
import os
import pathlib
import shutil
import sqlite3
import tempfile
import time

from . import almacen, cgx, exportacion, plataforma, rutas

MARGEN_DISCO = 64 << 20


class ErrorRestauracion(Exception):
    """No se restauró nada (o se explica qué quedó). El mensaje es apto para el usuario."""


def _borra_wal(destino, tras_reemplazar=False, espera_s=2.0):
    """En Windows no se puede borrar un -wal/-shm mientras CUALQUIER conexión SQLite lo tiene abierto
    (en POSIX el unlink no lo necesita libre). Reintenta un rato corto para los lectores transitorios
    (otra instancia leyendo un instante); si el bloqueo persiste, aborta igual sin tocar nada."""
    for sufijo in ("-wal", "-shm"):
        ruta = destino + sufijo
        if not os.path.exists(ruta):
            continue
        limite = time.monotonic() + espera_s
        while True:
            try:
                os.remove(ruta)
                break
            except FileNotFoundError:
                break           # otra conexión lo acaba de limpiar sola al cerrar (checkpoint final): ya no está, que es lo que queríamos
            except OSError as e:
                if time.monotonic() >= limite:
                    if tras_reemplazar:
                        raise ErrorRestauracion(f"la base YA se restauró, pero no se pudo borrar {os.path.basename(ruta)} ({type(e).__name__}): "
                                                "bórralo a mano antes de abrir la barra") from None
                    raise ErrorRestauracion(f"no se puede borrar {os.path.basename(ruta)} ({type(e).__name__}): cierra lo que use la base y "
                                            "reintenta; no se ha cambiado nada") from None
                time.sleep(0.1)
    return not any(os.path.exists(destino + s) for s in ("-wal", "-shm"))


def _comprueba(ruta):
    """Información de la base descifrada, o ErrorRestauracion. Se abre en solo lectura (el fichero está en journal_mode=DELETE)."""
    try:
        con = sqlite3.connect(f"{pathlib.Path(ruta).as_uri()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise ErrorRestauracion("el contenido descifrado no es una base SQLite") from None
    try:
        version = con.execute("PRAGMA user_version").fetchone()[0]
        if version > almacen.VERSION_ESQUEMA:
            raise ErrorRestauracion(f"la copia es de una versión más nueva ({version}) que esta aplicación; actualízala primero")
        if version < 1:
            raise ErrorRestauracion("el contenido descifrado no es una base de consumo")
        estado = str(con.execute("PRAGMA quick_check(1)").fetchone()[0])
        if estado != "ok":
            raise ErrorRestauracion(f"la copia descifrada está dañada (quick_check: {estado[:80]})")
        turnos = con.execute("SELECT COUNT(*) FROM turnos").fetchone()[0]
        return {"version": version, "turnos": turnos}
    except sqlite3.Error:
        raise ErrorRestauracion("el contenido descifrado no es una base de consumo válida") from None
    finally:
        con.close()


def _cabecera(ruta_cgx):
    if not os.path.isfile(ruta_cgx):
        raise ErrorRestauracion("la ruta de la copia no es un fichero")
    try:
        with open(ruta_cgx, "rb") as f:
            _modo, tipo, fecha, *_ = cgx.leer_cabecera(f)
    except cgx.ErrorCgx as e:
        raise ErrorRestauracion(str(e)) from None
    except OSError as e:
        raise ErrorRestauracion(f"no se puede leer la copia ({type(e).__name__})") from None
    return tipo, fecha


def _hay_sitio(ruta_cgx, directorio):
    """Un .cgx hostil y enorme no debe llenar el disco donde vive la base antes de rechazarse."""
    necesario = os.path.getsize(ruta_cgx) + MARGEN_DISCO
    if shutil.disk_usage(directorio).free < necesario:
        raise ErrorRestauracion("no hay espacio libre suficiente para descifrar esta copia; no se ha cambiado nada")


def _dir_temporal(destino):
    """El temporal va en `DATOS/tmp` (se barre) si está en el mismo sistema de ficheros que el destino (así `os.replace` es atómico)."""
    d = exportacion.dir_tmp()
    try:
        if os.stat(d).st_dev == os.stat(os.path.dirname(destino)).st_dev:
            return d
    except OSError:
        pass
    return os.path.dirname(destino)


def _aparta_danada(destino, ahora):
    """Mueve una base que no se puede abrir (y su -wal/-shm) a la carpeta de copias previas. Devuelve la ruta."""
    os.makedirs(rutas.COPIAS, mode=0o700, exist_ok=True)
    base = os.path.join(rutas.COPIAS, f"consumo.sqlite.danada-{time.strftime('%Y%m%d-%H%M%S', time.gmtime(ahora))}")
    for sufijo in ("", "-wal", "-shm"):
        if os.path.exists(destino + sufijo):
            exportacion.reemplazar(destino + sufijo, base + sufijo)
    return base


def _toma_puesto(con, quien, espera_s, ahora):
    limite = time.monotonic() + espera_s
    while True:
        if almacen.adquirir_escritura(con, quien, ahora=int(time.time()) if espera_s else ahora):
            return
        if time.monotonic() >= limite:
            raise ErrorRestauracion("la barra (u otro proceso) sigue escribiendo en la base: espera un minuto y reintenta, o párala con "
                                    "`launchctl bootout gui/$(id -u)/dev.codeia.costguard` (macOS)")
        time.sleep(1)


def restaurar(ruta_cgx, descifrar, confirmar, destino=None, forzar=False, ahora=None, espera_s=0):
    """`descifrar(entrada, salida)` hace el descifrado que toque (frase o clave privada) y lanza ErrorCgx si no cuadra.
    `confirmar(info)` recibe {fecha, tipo, turnos, version, sustituye} y devuelve True para seguir. `espera_s`: cuánto esperar a que
    la barra suelte el puesto de escritor. Devuelve un resumen."""
    reales = os.path.abspath(os.path.join(plataforma.dir_datos(), "consumo.sqlite"))
    if destino is None and plataforma.es_checkout_de_desarrollo(rutas.RAIZ) and os.path.abspath(rutas.ALMACEN) == reales:
        raise ErrorRestauracion("un checkout de desarrollo no restaura sobre los datos reales: define COSTGUARD_DATOS")
    destino = os.path.abspath(destino or rutas.ALMACEN)
    ahora = int(ahora if ahora is not None else time.time())
    existe = os.path.exists(destino)
    if existe and not forzar:
        raise ErrorRestauracion("ya hay una base en el destino: usa --forzar si quieres sustituirla (se guarda antes una copia)")
    tipo, fecha = _cabecera(ruta_cgx)
    os.makedirs(os.path.dirname(destino), mode=0o700, exist_ok=True)
    _hay_sitio(ruta_cgx, os.path.dirname(destino))
    quien, con, temporal, marcador = almacen.identidad("restauracion"), None, None, False
    try:
        if destino == os.path.abspath(rutas.ALMACEN):
            try:
                almacen.poner_marcador()
                marcador = True
            except almacen.ErrorAlmacen as e:
                raise ErrorRestauracion(str(e)) from None
        apartada = None
        if existe:
            try:
                con = almacen.abrir(destino)
            except (almacen.ErrorAlmacen, sqlite3.Error) as e:
                if isinstance(e, almacen.ErrorAlmacen) and "versión más nueva" in str(e):
                    raise ErrorRestauracion(str(e)) from None
                con = None                                           # base dañada: se aparta en el paso de sustitución
            if con is not None:
                _toma_puesto(con, quien, espera_s, ahora)
        fd, temporal = tempfile.mkstemp(prefix=".restaurando-", suffix=".tmp", dir=_dir_temporal(destino))
        try:
            with os.fdopen(fd, "wb") as salida, open(ruta_cgx, "rb") as entrada:
                descifrar(entrada, salida)
        except cgx.ErrorCgx as e:
            raise ErrorRestauracion(str(e)) from None
        info = _comprueba(temporal)
        info.update(fecha=fecha, tipo=tipo, sustituye=existe)
        if not confirmar(info):
            raise ErrorRestauracion("restauración cancelada; no se ha cambiado nada")
        previa = None
        if existe and con is not None:
            if not almacen.adquirir_escritura(con, quien, ahora=int(time.time())):          # el puesto sigue siendo nuestro
                raise ErrorRestauracion("se perdió el puesto de escritor durante la restauración; no se ha cambiado nada")
            previa = almacen.copia(con, rutas.COPIAS, prefijo="prerrestauracion")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            con.close()                                    # el puesto NO se suelta: la base se sustituye y la nueva trae el suyo
            con = None
        elif existe:
            previa = apartada = _aparta_danada(destino, ahora)
        if not _borra_wal(destino):
            raise ErrorRestauracion("quedan ficheros -wal/-shm de la base anterior; no se ha cambiado nada")
        try:
            exportacion.reemplazar(temporal, destino)
        except OSError as e:
            raise ErrorRestauracion(f"no se pudo sustituir la base ({type(e).__name__}): cierra lo que la use y reintenta."
                                    + (f" La base anterior está a salvo en {previa}" if previa else "")) from None
        temporal = None
        _borra_wal(destino, tras_reemplazar=True)
        try:
            almacen.abrir(destino).close()
        except (almacen.ErrorAlmacen, sqlite3.Error) as e:
            raise ErrorRestauracion(f"la base restaurada no se abre ({type(e).__name__})") from None
        return {"turnos": info["turnos"], "fecha_copia": fecha, "sustituyo": existe, "destino": destino, "copia_previa": previa,
                "apartada": bool(apartada)}
    finally:
        if con is not None:
            try:
                almacen.soltar_escritura(con, quien)
            except Exception:                                # noqa: BLE001
                pass
            con.close()
        if temporal:
            exportacion._borra(temporal)
        if marcador:
            almacen.quitar_marcador()


def probar(ruta_cgx, descifrar):
    """Ensayo de restauración: descifra y comprueba en un temporal privado de `DATOS/tmp` y devuelve {turnos, version, fecha, tipo}.
    No toca la base ni crea nada permanente."""
    tipo, fecha = _cabecera(ruta_cgx)
    _hay_sitio(ruta_cgx, exportacion.dir_tmp())
    fd, temporal = tempfile.mkstemp(prefix="prueba-", suffix=".tmp", dir=exportacion.dir_tmp())
    try:
        try:
            with os.fdopen(fd, "wb") as salida, open(ruta_cgx, "rb") as entrada:
                descifrar(entrada, salida)
        except cgx.ErrorCgx as e:
            raise ErrorRestauracion(str(e)) from None
        info = _comprueba(temporal)
        info.update(fecha=fecha, tipo=tipo)
        return info
    finally:
        exportacion._borra(temporal)

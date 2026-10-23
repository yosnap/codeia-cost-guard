"""Contexto TLS que verifica con el almacén de certificados disponible en este Mac.

El Python de python.org en macOS no trae el almacén del sistema: sin esto, cualquier https falla con
CERTIFICATE_VERIFY_FAILED (las webs salen «sin respuesta» aunque funcionen). Se prueba certifi y, si
no, el bundle que macOS deja en /etc/ssl/cert.pem. Nunca se desactiva la verificación.
"""
import os, ssl

_BUNDLES = ("/etc/ssl/cert.pem", "/usr/local/etc/openssl/cert.pem", "/opt/homebrew/etc/openssl/cert.pem")


def contexto_ssl():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for cafile in _BUNDLES:
        if os.path.exists(cafile):
            try:
                return ssl.create_default_context(cafile=cafile)
            except Exception:
                pass
    return ssl.create_default_context()

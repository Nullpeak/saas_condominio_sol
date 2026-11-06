"""
Enlaces "públicos" para correos (invitaciones, recuperar contraseña).

Si alguien usa la app entrando por localhost/127.0.0.1, un enlace armado con esa
dirección no le sirve a quien recibe el correo. Por eso:

1. Si existe la variable SITIO_URL (ej. SITIO_URL=http://192.168.1.25:8000 en el .env),
   se usa siempre esa dirección.
2. Si no, y se entró por localhost, se reemplaza por la IP del equipo en la red local
   (mismo puerto).
3. En cualquier otro caso se usa la dirección con la que se entró.
"""
import socket
from urllib.parse import urlsplit

from django.conf import settings

LOCALES = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


def ip_red_local():
    """IP de este equipo en la red local (no envía nada: solo elige la interfaz de salida)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def base_publica(request):
    """Devuelve (protocolo, dominio) para armar enlaces que funcionen en otros equipos."""
    sitio = getattr(settings, "SITIO_URL", "")
    if sitio:
        partes = urlsplit(sitio)
        return partes.scheme or "http", partes.netloc
    protocolo = "https" if request.is_secure() else "http"
    host = request.get_host()
    nombre, _, puerto = host.rpartition(":") if host.count(":") == 1 else (host, "", "")
    if nombre in LOCALES:
        host = ip_red_local() + (f":{puerto}" if puerto else "")
    return protocolo, host


def url_publica(request, ruta):
    protocolo, dominio = base_publica(request)
    return f"{protocolo}://{dominio}{ruta}"

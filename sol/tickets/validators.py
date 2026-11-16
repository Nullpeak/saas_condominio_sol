import mimetypes
import os

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError


MAX_ADJUNTO_BYTES = 25 * 1024 * 1024
EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
EXTENSIONES_VIDEO = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}


def validar_adjunto(archivo):
    extension = os.path.splitext(archivo.name)[1].lower()
    if archivo.size > MAX_ADJUNTO_BYTES:
        raise ValidationError("Cada archivo debe pesar 25 MB o menos.")

    if extension in EXTENSIONES_IMAGEN:
        tipo = getattr(archivo, "content_type", "") or mimetypes.guess_type(archivo.name)[0] or ""
        if not tipo.startswith("image/"):
            raise ValidationError("El archivo no parece ser una imagen válida.")
        try:
            imagen = Image.open(archivo)
            imagen.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise ValidationError("La imagen está dañada o no es válida.")
        finally:
            archivo.seek(0)
        return

    if extension not in EXTENSIONES_VIDEO:
        raise ValidationError("Solo se permiten imágenes o videos.")

    tipo = getattr(archivo, "content_type", "") or mimetypes.guess_type(archivo.name)[0] or ""
    if tipo and not tipo.startswith("video/") and tipo != "application/octet-stream":
        raise ValidationError("El archivo no parece ser un video válido.")

    encabezado = archivo.read(32)
    archivo.seek(0)
    if extension in {".mp4", ".m4v", ".mov"} and b"ftyp" not in encabezado[4:16]:
        raise ValidationError("El archivo no contiene un video MP4 o MOV válido.")
    if extension in {".webm", ".mkv"} and not encabezado.startswith(bytes.fromhex("1A45DFA3")):
        raise ValidationError("El archivo no contiene un video WebM o MKV válido.")
    if extension == ".avi" and not (encabezado.startswith(b"RIFF") and b"AVI " in encabezado[8:16]):
        raise ValidationError("El archivo no contiene un video AVI válido.")

import mimetypes
import os

from django.core.exceptions import ValidationError


MAX_DOCUMENTO_BYTES = 25 * 1024 * 1024
EXTENSIONES_PERMITIDAS = {".pdf", ".jpg", ".jpeg", ".png", ".webp"}


def validar_documento(archivo):
    extension = os.path.splitext(archivo.name)[1].lower()
    if extension not in EXTENSIONES_PERMITIDAS:
        raise ValidationError("Adjunta un PDF o una imagen (JPG, PNG o WEBP).")
    if archivo.size > MAX_DOCUMENTO_BYTES:
        raise ValidationError("Cada archivo debe pesar 25 MB o menos.")
    tipo = getattr(archivo, "content_type", "") or mimetypes.guess_type(archivo.name)[0] or ""
    if extension == ".pdf" and tipo not in ("application/pdf", "application/octet-stream"):
        raise ValidationError("El archivo no parece ser un PDF válido.")
    if extension != ".pdf" and not tipo.startswith("image/"):
        raise ValidationError("El archivo no parece ser una imagen válida.")

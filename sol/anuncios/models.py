import os

from django.core.exceptions import ValidationError
from django.db import models
from PIL import Image, UnidentifiedImageError


MAX_IMAGEN_BYTES = 10 * 1024 * 1024
EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def validar_imagen(archivo):
    extension = os.path.splitext(archivo.name)[1].lower()
    if extension not in EXTENSIONES_IMAGEN:
        raise ValidationError("Adjunta una imagen JPG, PNG, GIF, WebP o BMP.")
    if archivo.size > MAX_IMAGEN_BYTES:
        raise ValidationError("La imagen debe pesar 10 MB o menos.")
    try:
        imagen = Image.open(archivo)
        imagen.verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("La imagen está dañada o no es válida.")
    finally:
        archivo.seek(0)


def ruta_imagen(instance, filename):
    return f"anuncios/{instance.condominio_id}/{os.path.basename(filename)}"


class AnuncioOficial(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        PUBLICADO = "publicado", "Publicado"
        ARCHIVADO = "archivado", "Archivado"

    class Alcance(models.TextChoices):
        TODOS = "todos", "Todo el condominio"
        EDIFICIOS = "edificios", "Edificios seleccionados"
        UNIDADES = "unidades", "Unidades seleccionadas"

    condominio = models.ForeignKey("core.Condominio", on_delete=models.CASCADE, related_name="anuncios_oficiales")
    autor = models.ForeignKey("core.Usuario", on_delete=models.PROTECT, related_name="anuncios_oficiales_creados")
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to=ruta_imagen, blank=True, validators=[validar_imagen])
    alcance = models.CharField(max_length=12, choices=Alcance.choices, default=Alcance.TODOS)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PUBLICADO)
    edificios = models.ManyToManyField("core.Edificio", blank=True, related_name="anuncios_oficiales")
    unidades = models.ManyToManyField("core.Unidad", blank=True, related_name="anuncios_oficiales")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-creado", "-id"]

    def __str__(self):
        return self.titulo


class AnuncioResidente(models.Model):
    class Alcance(models.TextChoices):
        CONDOMINIO = "condominio", "Todo el condominio"
        EDIFICIO = "edificio", "Mi edificio"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de revisión"
        PUBLICADO = "publicado", "Publicado"
        RECHAZADO = "rechazado", "No aprobado"
        ARCHIVADO = "archivado", "Archivado"

    condominio = models.ForeignKey("core.Condominio", on_delete=models.CASCADE, related_name="anuncios_residentes")
    autor = models.ForeignKey("core.Usuario", on_delete=models.PROTECT, related_name="anuncios_residentes_creados")
    unidad = models.ForeignKey("core.Unidad", on_delete=models.PROTECT, related_name="anuncios_residentes")
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    imagen = models.ImageField(upload_to=ruta_imagen, blank=True, validators=[validar_imagen])
    alcance = models.CharField(max_length=12, choices=Alcance.choices, default=Alcance.EDIFICIO)
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.PENDIENTE)
    moderado_por = models.ForeignKey("core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="anuncios_residentes_moderados")
    creado = models.DateTimeField(auto_now_add=True)
    moderado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado", "-id"]

    def __str__(self):
        return self.titulo

    def clean(self):
        super().clean()
        if self.unidad_id and self.unidad.edificio.condominio_id != self.condominio_id:
            raise ValidationError({"unidad": "La unidad debe pertenecer al condominio del anuncio."})


class LecturaAnuncioOficial(models.Model):
    usuario = models.ForeignKey("core.Usuario", on_delete=models.CASCADE, related_name="lecturas_anuncios_oficiales")
    anuncio = models.ForeignKey(AnuncioOficial, on_delete=models.CASCADE, related_name="lecturas")
    leido = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "anuncio"], name="lectura_oficial_usuario_unica")]


class LecturaAnuncioResidente(models.Model):
    usuario = models.ForeignKey("core.Usuario", on_delete=models.CASCADE, related_name="lecturas_anuncios_residentes")
    anuncio = models.ForeignKey(AnuncioResidente, on_delete=models.CASCADE, related_name="lecturas")
    leido = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["usuario", "anuncio"], name="lectura_residente_usuario_unica")]

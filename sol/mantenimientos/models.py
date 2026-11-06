import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models

from .validators import validar_documento


def documento_mantenimiento_path(instance, filename):
    extension = os.path.splitext(filename)[1].lower()
    return (
        f"mantenimientos/{instance.registro.elemento.condominio_id}/"
        f"{instance.registro.elemento_id}/{instance.registro_id}/"
        f"{uuid.uuid4().hex}{extension}"
    )


class ElementoMantenimiento(models.Model):
    condominio = models.ForeignKey(
        "core.Condominio", on_delete=models.CASCADE, related_name="elementos_mantenimiento"
    )
    categoria = models.CharField(max_length=80)
    nombre = models.CharField(max_length=150)
    ubicacion = models.CharField(max_length=150, blank=True)
    empresa_actual = models.CharField(max_length=150, blank=True)
    telefono_actual = models.CharField(max_length=40, blank=True)
    correo_actual = models.EmailField(blank=True)
    notas = models.TextField(blank=True)
    creado_por = models.ForeignKey(
        "core.Usuario", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="elementos_mantenimiento_creados",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mantenimiento_elemento"
        ordering = ["categoria", "nombre"]
        constraints = [
            models.UniqueConstraint(
                fields=["condominio", "nombre"], name="mantenimiento_elemento_nombre_condominio"
            )
        ]

    def __str__(self):
        return f"{self.categoria}: {self.nombre}"

    def clean(self):
        super().clean()
        self.categoria = self.categoria.strip()
        self.nombre = self.nombre.strip()
        if not self.categoria:
            raise ValidationError({"categoria": "Indica una categoría, por ejemplo piscina o ascensor."})


class RegistroMantenimiento(models.Model):
    class Estado(models.TextChoices):
        PROGRAMADO = "programado", "Programado"
        EN_CURSO = "en_curso", "En curso"
        COMPLETADO = "completado", "Completado"
    elemento = models.ForeignKey(
        ElementoMantenimiento, on_delete=models.CASCADE, related_name="registros"
    )
    fecha = models.DateField()
    descripcion = models.TextField()
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.COMPLETADO)
    detalle_cierre = models.TextField(blank=True)
    empresa = models.CharField(max_length=150, blank=True)
    telefono_empresa = models.CharField(max_length=40, blank=True)
    proximo_mantenimiento = models.DateField(null=True, blank=True)
    # Fecha para la que ya se avisó (tarea "avisar_mantenciones_proximas"); si cambia, se vuelve a avisar
    proximo_notificado = models.DateField(null=True, blank=True, editable=False)
    creado_por = models.ForeignKey(
        "core.Usuario", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="registros_mantenimiento_creados",
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "mantenimiento_registro"
        ordering = ["-fecha", "-id"]

    def __str__(self):
        return f"{self.elemento} · {self.fecha:%d/%m/%Y}"

    def clean(self):
        super().clean()
        if self.proximo_mantenimiento and self.proximo_mantenimiento < self.fecha:
            raise ValidationError({
                "proximo_mantenimiento": "La próxima fecha debe ser igual o posterior a la fecha del servicio."
            })


class DocumentoMantenimiento(models.Model):
    class Tipo(models.TextChoices):
        BOLETA = "boleta", "Boleta o factura"
        CERTIFICADO = "certificado", "Certificado"
        OTRO = "otro", "Otro documento"

    registro = models.ForeignKey(
        RegistroMantenimiento, on_delete=models.CASCADE, related_name="documentos"
    )
    tipo = models.CharField(max_length=15, choices=Tipo.choices, default=Tipo.OTRO)
    nombre = models.CharField(max_length=150, blank=True)
    archivo = models.FileField(upload_to=documento_mantenimiento_path, validators=[validar_documento])
    subido_por = models.ForeignKey(
        "core.Usuario", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="documentos_mantenimiento_subidos",
    )
    subido = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "mantenimiento_documento"
        ordering = ["subido", "id"]

    def __str__(self):
        return self.nombre or self.get_tipo_display()

from django.db import models
from django.utils import timezone


class Visita(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Programada"
        PRESENTE = "presente", "Presente"
        SIN_REGISTRO = "sin_registro", "Sin registro"
        FINALIZADA = "finalizada", "Salida registrada"
        CANCELADA = "cancelada", "Cancelada"

    condominio = models.ForeignKey(
        "core.Condominio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitas",
    )
    edificio = models.ForeignKey(
        "core.Edificio",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitas",
    )
    unidad = models.ForeignKey(
        "core.Unidad",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitas",
    )
    registrado_por = models.ForeignKey(
        "core.Usuario",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitas_registradas",
    )
    nombre = models.CharField(max_length=100)
    fecha_visita = models.DateField(default=timezone.localdate, db_index=True)
    motivo = models.CharField(max_length=160, blank=True)
    patente_vehiculo = models.CharField(max_length=10, blank=True)
    tiempo_llegada = models.DateTimeField(null=True, blank=True)
    tiempo_salida = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(
        max_length=15,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
        db_index=True,
    )

    class Meta:
        db_table = "visita"
        ordering = ["fecha_visita", "nombre"]
        indexes = [models.Index(fields=["condominio", "fecha_visita"], name="visita_condominio_fecha_idx")]

    def __str__(self):
        return f"{self.nombre} - {self.unidad or 'Unidad no disponible'} ({self.fecha_visita})"

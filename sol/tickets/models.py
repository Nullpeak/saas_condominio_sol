from django.core.exceptions import ValidationError
from django.db import models

from .validators import validar_adjunto


def ticket_archivo_path(instance, filename):
    return f"tickets/{instance.ticket_id}/{filename}"


class Ticket(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Pendiente"
        EN_REVISION = "en_revision", "En revisión"
        RESUELTO = "resuelto", "Resuelto"
        CERRADO = "cerrado", "Cerrado"
        CANCELADO = "cancelado", "Cancelado"

    class Categoria(models.TextChoices):
        AGUA = "agua", "Agua o filtración"
        ELECTRICIDAD = "electricidad", "Electricidad"
        INFRAESTRUCTURA = "infraestructura", "Infraestructura"
        RUIDO = "ruido", "Ruido o convivencia"
        OTRO = "otro", "Otro"

    class Prioridad(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"

    condominio = models.ForeignKey("core.Condominio", on_delete=models.CASCADE, related_name="tickets")
    unidad = models.ForeignKey("core.Unidad", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    usuario = models.ForeignKey("core.Usuario", on_delete=models.PROTECT, related_name="tickets_creados")
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    categoria = models.CharField(max_length=20, choices=Categoria.choices, default=Categoria.OTRO)
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ABIERTO)
    prioridad = models.CharField(max_length=5, choices=Prioridad.choices, blank=True)
    asignado = models.ForeignKey("core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets_asignados")
    resuelto_por = models.ForeignKey("core.Usuario", on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets_resueltos")
    creado = models.DateTimeField(auto_now_add=True)
    resuelto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ticket"
        ordering = ["-creado", "-id"]
        indexes = [models.Index(fields=["estado"], name="ticket_estado_880f6e_idx")]

    def __str__(self):
        return f"#{self.pk} - {self.titulo}"

    def clean(self):
        super().clean()
        if self.unidad_id and self.unidad.edificio.condominio_id != self.condominio_id:
            raise ValidationError({"unidad": "La unidad debe pertenecer al condominio del ticket."})
        if self.asignado_id and self.asignado.rol != "Gestor":
            raise ValidationError({"asignado": "Solo se puede asignar el ticket a un Gestor."})
        if self.asignado_id and self.asignado.condominio_id != self.condominio_id:
            raise ValidationError({"asignado": "El gestor asignado debe pertenecer a este condominio."})


class TicketArchivo(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="archivos")
    archivo = models.FileField(upload_to=ticket_archivo_path, validators=[validar_adjunto])
    subido = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_archivo"
        ordering = ["subido", "id"]

    def __str__(self):
        return f"Adjunto de ticket #{self.ticket_id}"


class TicketHistorial(models.Model):
    class Tipo(models.TextChoices):
        CREADO = "creado", "Ticket creado"
        EDICION = "edicion", "Ticket actualizado"
        ESTADO = "estado", "Cambio de estado"
        ASIGNACION = "asignacion", "Cambio de asignación"
        RESPUESTA = "respuesta", "Respuesta"
        CANCELACION = "cancelacion", "Ticket cancelado"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="historial")
    usuario = models.ForeignKey("core.Usuario", null=True, blank=True, on_delete=models.SET_NULL, related_name="historial_tickets")
    tipo = models.CharField(max_length=15, choices=Tipo.choices)
    detalle = models.TextField(blank=True)
    estado_anterior = models.CharField(max_length=15, blank=True)
    estado_nuevo = models.CharField(max_length=15, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_historial"
        ordering = ["creado", "id"]

    def __str__(self):
        return f"{self.get_tipo_display()} · Ticket #{self.ticket_id}"

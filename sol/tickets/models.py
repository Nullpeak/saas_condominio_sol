import os
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import Condominio, Unidad, Usuario
from .validators import EXTENSIONES_IMAGEN, EXTENSIONES_VIDEO

ROLES_ASIGNABLES = (Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR)


class Ticket(models.Model):
    """
    Un inconveniente reportado en el condominio.
    La tabla "ticket" ya existía en core: se movió a esta app sin recrearla (ver migración 0001).
    """

    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        EN_REVISION = "en_revision", "En revisión"
        ESPERANDO = "esperando", "Esperando respuesta"
        RESUELTO = "resuelto", "Resuelto"
        CERRADO = "cerrado", "Cerrado"

    class Prioridad(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"

    class Categoria(models.TextChoices):
        VISITAS = "visitas", "Registro de visitas"
        MANTENCION = "mantencion", "Mantención / reparaciones"
        AREAS_COMUNES = "areas_comunes", "Áreas comunes"
        SEGURIDAD = "seguridad", "Seguridad"
        LIMPIEZA = "limpieza", "Aseo y basura"
        CONVIVENCIA = "convivencia", "Ruidos y convivencia"
        ESTACIONAMIENTO = "estacionamiento", "Estacionamientos"
        ADMINISTRACION = "administracion", "Administración y pagos"
        OTRO = "otro", "Otro"

    # Estados en que el ticket sigue "vivo" (cuenta como pendiente)
    ABIERTOS = (Estado.ABIERTO, Estado.EN_REVISION, Estado.ESPERANDO)

    condominio = models.ForeignKey(Condominio, on_delete=models.CASCADE, related_name="tickets")
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="tickets_creados")
    unidad = models.ForeignKey(Unidad, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets")
    categoria = models.CharField(max_length=20, choices=Categoria.choices, default=Categoria.OTRO)
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ABIERTO)
    prioridad = models.CharField(max_length=5, choices=Prioridad.choices, blank=True)
    asignado = models.ForeignKey(
        Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="tickets_asignados"
    )
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(default=timezone.now)  # último movimiento (comentario, cambio...)
    resuelto = models.DateTimeField(null=True, blank=True)
    cerrado = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ticket"
        ordering = ["-actualizado"]
        indexes = [
            models.Index(fields=["estado"]),
            models.Index(fields=["condominio", "estado"]),
        ]

    def __str__(self):
        return f"#{self.pk} - {self.titulo}"

    @property
    def esta_abierto(self):
        return self.estado in self.ABIERTOS

    def clean(self):
        super().clean()
        if self.asignado_id:
            a = self.asignado
            if a.rol not in ROLES_ASIGNABLES:
                raise ValidationError("Solo se puede asignar a un gestor o administrador.")
            if not a.puede_acceder_condominio(self.condominio_id):
                raise ValidationError("El usuario asignado no pertenece a este condominio.")
        if self.unidad_id and self.unidad.edificio.condominio_id != self.condominio_id:
            raise ValidationError("La unidad no pertenece a este condominio.")


class TicketComentario(models.Model):
    """Mensajes de la conversación. Los internos solo los ve el staff."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comentarios")
    autor = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="comentarios_ticket")
    texto = models.TextField()
    interno = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_comentario"
        ordering = ["creado"]

    def __str__(self):
        return f"Comentario de {self.autor} en #{self.ticket_id}"


class TicketEvento(models.Model):
    """Historial: quién cambió qué y cuándo. Se escribe solo desde services.py."""

    class Tipo(models.TextChoices):
        CREADO = "creado", "Creado"
        EDICION = "edicion", "Edición"
        ESTADO = "estado", "Estado"
        PRIORIDAD = "prioridad", "Prioridad"
        ASIGNADO = "asignado", "Asignación"
        CATEGORIA = "categoria", "Categoría"
        VISITA = "visita", "Visita"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="eventos")
    autor = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    anterior = models.CharField(max_length=100, blank=True)  # texto legible, no el código
    nuevo = models.CharField(max_length=100, blank=True)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_evento"
        ordering = ["creado"]

    def __str__(self):
        return f"#{self.ticket_id} {self.tipo}: {self.anterior} → {self.nuevo}"

    @property
    def texto(self):
        if self.tipo == self.Tipo.CREADO:
            return "creó el ticket"
        if self.tipo == self.Tipo.EDICION:
            return "editó el asunto, la descripción o la categoría"
        if self.tipo == self.Tipo.VISITA:
            return f"{self.nuevo} la visita de {self.anterior}"
        if self.tipo == self.Tipo.ASIGNADO:
            return f"asignó el ticket a {self.nuevo}" if self.nuevo else "quitó la asignación"
        nombre = self.get_tipo_display().lower()
        if not self.anterior:
            return f"definió {nombre}: {self.nuevo}"
        return f"cambió {nombre} de «{self.anterior}» a «{self.nuevo}»"


def ticket_archivo_path(instance, filename):
    # Prefijo aleatorio: dos archivos con el mismo nombre no se pisan y la ruta no se puede adivinar
    base, ext = os.path.splitext(os.path.basename(filename))
    return f"tickets/{instance.ticket_id}/{uuid.uuid4().hex[:12]}_{base[:60]}{ext.lower()}"


class TicketArchivo(models.Model):
    """Fotos o documentos adjuntos. Se descargan por una vista que revisa permisos."""

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="pruebas")
    comentario = models.ForeignKey(
        TicketComentario, on_delete=models.CASCADE, null=True, blank=True, related_name="archivos"
    )
    archivo = models.FileField(upload_to=ticket_archivo_path)
    nombre = models.CharField(max_length=150, blank=True)  # nombre original, para mostrar
    subido = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_archivo"

    def __str__(self):
        return self.nombre or f"Prueba de ticket #{self.ticket_id}"

    @property
    def es_imagen(self):
        return os.path.splitext(self.archivo.name)[1].lower() in EXTENSIONES_IMAGEN

    @property
    def es_video(self):
        return os.path.splitext(self.archivo.name)[1].lower() in EXTENSIONES_VIDEO


class TicketVisita(models.Model):
    """
    Visita que un residente pide registrar desde un ticket de categoría "visitas".
    Queda pendiente hasta que el staff la aprueba: recién ahí se crea la Visita
    programada en la app "visitas" (la que ve conserjería).
    """

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de aprobación"
        APROBADA = "aprobada", "Aprobada"
        RECHAZADA = "rechazada", "Rechazada"

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="visitas")
    nombre = models.CharField(max_length=100)
    rut = models.CharField(max_length=12, blank=True)
    patente = models.CharField(max_length=10, blank=True)
    llegada = models.DateTimeField()
    salida = models.DateTimeField()
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    motivo_rechazo = models.CharField(max_length=255, blank=True)
    revisada_por = models.ForeignKey(Usuario, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    revisada = models.DateTimeField(null=True, blank=True)
    visita = models.OneToOneField(
        "visitas.Visita", on_delete=models.SET_NULL, null=True, blank=True, related_name="ticket_visita"
    )

    class Meta:
        db_table = "ticket_visita"
        ordering = ["llegada", "pk"]

    def __str__(self):
        return f"{self.nombre} ({self.get_estado_display()}) - ticket #{self.ticket_id}"

    def clean(self):
        super().clean()
        if self.llegada and self.salida and self.salida <= self.llegada:
            raise ValidationError("La salida debe ser posterior a la llegada.")

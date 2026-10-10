import hashlib
import secrets
from datetime import timedelta

from django.db import models
from django.utils import timezone

from core.models import Condominio, Unidad, Usuario

VIGENCIA = timedelta(days=3)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Invitacion(models.Model):
    """
    Invitación de un solo uso. NO existe cuenta hasta que la persona completa el formulario.
    En la BD solo se guarda el hash del token: si alguien lee la tabla, no puede reconstruir los links.
    """

    class Relacion(models.TextChoices):
        # Los valores coinciden con los campos de Unidad (dueno / arrendatario)
        DUENO = "dueno", "Dueño"
        ARRENDATARIO = "arrendatario", "Arrendatario"

    email = models.EmailField(max_length=50)
    rol = models.CharField(max_length=30, choices=Usuario.Rol.choices)
    condominio = models.ForeignKey(Condominio, on_delete=models.CASCADE, related_name="invitaciones")
    # Solo para residentes: la unidad donde vivirá y en qué calidad
    unidad = models.ForeignKey(Unidad, on_delete=models.CASCADE, null=True, blank=True, related_name="invitaciones")
    relacion = models.CharField(max_length=15, choices=Relacion.choices, blank=True)
    invitado_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="invitaciones_enviadas")
    token_hash = models.CharField(max_length=64, unique=True)
    creada = models.DateTimeField(auto_now_add=True)
    expira = models.DateTimeField()
    usada = models.DateTimeField(null=True, blank=True)
    revocada = models.BooleanField(default=False)

    class Meta:
        db_table = "invitacion"
        indexes = [models.Index(fields=["email"])]

    def __str__(self):
        return f"{self.email} ({self.rol})"

    @classmethod
    def crear(cls, **campos):
        """Devuelve (invitacion, token_en_claro). El token en claro solo existe para el correo."""
        token = secrets.token_urlsafe(32)  # 256 bits: no se puede adivinar
        inv = cls.objects.create(
            token_hash=hash_token(token),
            expira=timezone.now() + VIGENCIA,
            **campos,
        )
        return inv, token

    @classmethod
    def vigentes(cls):
        return cls.objects.filter(usada__isnull=True, revocada=False, expira__gt=timezone.now())

    @classmethod
    def buscar(cls, token: str):
        return (
            cls.vigentes()
            .filter(token_hash=hash_token(token))
            .select_related("condominio", "unidad__edificio")
            .first()
        )
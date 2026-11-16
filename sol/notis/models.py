from django.conf import settings
from django.db import models

# Create your models here.

class Notification(models.Model):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="notifications_received",
        on_delete=models.CASCADE,
    )
    # null = notificación generada por el sistema
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="notifications_sent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    message = models.TextField()
    url = models.CharField(max_length=255, blank=True)  # a dónde lleva al hacer clic
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notificacion"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["recipient", "is_read"])]

    def __str__(self):
        return f"Notificación para {self.recipient} ({'leída' if self.is_read else 'nueva'})"
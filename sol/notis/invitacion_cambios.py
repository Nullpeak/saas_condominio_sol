# Agregar a la clase Invitacion (app invitaciones/models.py), luego makemigrations + migrate

    # Evitan avisar dos veces el mismo evento
    uso_notificado = models.BooleanField(default=False)
    expiracion_notificada = models.BooleanField(default=False)

    @property
    def estado(self):
        if self.usada:
            return "usada"
        if self.revocada:
            return "revocada"
        if self.expira <= timezone.now():
            return "expirada"
        return "pendiente"

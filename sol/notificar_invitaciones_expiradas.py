from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from invitaciones.models import Invitacion
from notifications.signals import avisar_invitacion_usada, notificar


class Command(BaseCommand):
    help = "Avisa a quien invitó cuando una invitación fue usada o expiró sin usarse."

    def handle(self, *args, **options):
        # Red de seguridad: invitaciones usadas cuyo aviso no salió por señal
        usadas = Invitacion.objects.filter(
            usada__isnull=False, uso_notificado=False
        ).select_related("invitado_por__user")
        n_usadas = 0
        for inv in usadas:
            avisar_invitacion_usada(inv)
            n_usadas += 1
        self.stdout.write(f"{n_usadas} invitaciones usadas notificadas.")

        pendientes = Invitacion.objects.filter(
            usada__isnull=True,
            revocada=False,
            expira__lte=timezone.now(),
            expiracion_notificada=False,
        ).select_related("invitado_por__user")

        total = 0
        for inv in pendientes:
            with transaction.atomic():
                notificar(
                    [inv.invitado_por],
                    f"La invitación enviada a {inv.email} "
                    f"({inv.get_rol_display()}) expiró sin ser utilizada.",
                )
                inv.expiracion_notificada = True
                inv.save(update_fields=["expiracion_notificada"])
            total += 1

        self.stdout.write(self.style.SUCCESS(f"{total} invitaciones expiradas notificadas."))

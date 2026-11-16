from django.core.management.base import BaseCommand

from notis.avisos import avisar_invitaciones


class Command(BaseCommand):
    help = "Avisa a quien invitó cuando una invitación fue usada o expiró sin usarse (lo mismo que hace Celery)."

    def handle(self, *args, **options):
        r = avisar_invitaciones()
        self.stdout.write(self.style.SUCCESS(
            f"{r['usadas']} invitaciones usadas y {r['expiradas']} expiradas notificadas."
        ))

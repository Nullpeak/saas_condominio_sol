from django.core.management.base import BaseCommand

from notis.avisos import avisar_mantenciones_proximas


class Command(BaseCommand):
    help = "Avisa al staff las mantenciones de los próximos días (lo mismo que hace Celery)."

    def add_arguments(self, parser):
        parser.add_argument("--dias", type=int, default=3)

    def handle(self, *args, dias, **options):
        n = avisar_mantenciones_proximas(dias)
        self.stdout.write(self.style.SUCCESS(f"{n} mantenciones próximas notificadas."))

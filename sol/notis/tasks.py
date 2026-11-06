"""Tareas de Celery. La programación está en CELERY_BEAT_SCHEDULE (settings.py)."""
from celery import shared_task

from . import avisos


@shared_task
def avisar_invitaciones():
    return avisos.avisar_invitaciones()


@shared_task
def avisar_mantenciones_proximas(dias=3):
    return avisos.avisar_mantenciones_proximas(dias)

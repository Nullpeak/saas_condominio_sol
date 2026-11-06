"""
Configuración de Celery para el proyecto.

Celery ejecuta tareas en segundo plano y, con "beat", tareas programadas
(por ejemplo, avisar invitaciones vencidas o mantenciones próximas).

Cómo correrlo (desde la carpeta sol/, en dos terminales):
    celery -A sol worker -l info --pool=solo     # ejecuta las tareas (--pool=solo es necesario en Windows)
    celery -A sol beat -l info                   # las dispara según CELERY_BEAT_SCHEDULE

Por defecto la cola de mensajes usa archivos en sol/.celery/ (no necesita instalar
Redis ni RabbitMQ). Para usar Redis: set CELERY_BROKER_URL=redis://localhost:6379/0
"""
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sol.settings")

app = Celery("sol")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

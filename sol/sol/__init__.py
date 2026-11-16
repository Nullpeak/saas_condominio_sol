# Carga Celery junto con Django para que @shared_task use esta configuración.
# Si Celery todavía no está instalado (pipenv install), Django funciona igual.
try:
    from .celery import app as celery_app
except ImportError:  # pragma: no cover
    celery_app = None

__all__ = ("celery_app",)

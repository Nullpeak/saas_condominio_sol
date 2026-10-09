from functools import wraps

from django.contrib.auth import get_user_model
from django.http import Http404

from core.models import Condominio


def setup_disponible():
    """El setup solo existe mientras no haya ningún usuario ni condominio."""
    User = get_user_model()
    return not User.objects.exists() and not Condominio.objects.exists()


def solo_primer_lanzamiento(view):
    """Si el sistema ya fue configurado, la URL responde 404 (como si no existiera)."""

    @wraps(view)
    def envoltura(request, *args, **kwargs):
        if not setup_disponible():
            raise Http404
        return view(request, *args, **kwargs)

    return envoltura
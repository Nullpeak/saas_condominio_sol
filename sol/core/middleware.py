from .audit import asignar_actor, limpiar_actor
from django.core.exceptions import ObjectDoesNotExist


class ActorAuditoriaMiddleware:
    """Makes the authenticated application profile available to audit signals."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        actor = None
        if request.user.is_authenticated:
            try:
                perfil = request.user.usuario
                if perfil.activo:
                    actor = perfil
            except (AttributeError, ObjectDoesNotExist):
                pass
        token = asignar_actor(actor)
        try:
            return self.get_response(request)
        finally:
            limpiar_actor(token)

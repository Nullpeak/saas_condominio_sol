from .models import Notification


def unread_count(request):
    """Deja `notif_unread_count` disponible en todos los templates (lo usa el base)."""
    if request.user.is_authenticated:
        return {
            "notif_unread_count": Notification.objects.filter(
                recipient=request.user, is_read=False
            ).count()
        }
    return {}
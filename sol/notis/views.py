from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Notification

DROPDOWN_MAX = 8
POR_PAGINA = 15


def _es_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _redirect_seguro(request, default):
    """Vuelve a la página de origen (Referer) solo si es del mismo sitio."""
    destino = request.META.get("HTTP_REFERER")
    if destino and url_has_allowed_host_and_scheme(
        destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(destino)
    return redirect(default)


@login_required
def notifications_list(request):
    propias = Notification.objects.filter(recipient=request.user)
    no_leidas = propias.filter(is_read=False)

    # Dropdown de la campana (AJAX): últimas no leídas
    if _es_ajax(request):
        html = render_to_string(
            "notis/notification_dropdown.html",
            {"notifications": no_leidas.select_related("sender")[:DROPDOWN_MAX]},
            request=request,
        )
        return JsonResponse({"html": html, "count": no_leidas.count()})

    # Página completa: todas, con filtro opcional y paginación
    filtro = request.GET.get("filtro", "todas")
    queryset = no_leidas if filtro == "no_leidas" else propias
    page = Paginator(queryset.select_related("sender"), POR_PAGINA).get_page(
        request.GET.get("page")
    )
    return render(
        request,
        "notis/list.html",
        {"page": page, "filtro": filtro, "total_no_leidas": no_leidas.count()},
    )


@login_required
def notifications_count(request):
    """Endpoint liviano para el polling del badge."""
    count = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({"count": count})


@login_required
@require_POST
def mark_as_read(request, notification_id):
    # Solo se pueden marcar las notificaciones propias
    notification = get_object_or_404(Notification, id=notification_id, recipient=request.user)
    if not notification.is_read:
        notification.is_read = True
        notification.save(update_fields=["is_read"])

    if _es_ajax(request):
        return JsonResponse({"ok": True})
    if notification.url:
        return redirect(notification.url)
    return _redirect_seguro(request, "notifications_list")


@login_required
@require_POST
def mark_all_as_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    if _es_ajax(request):
        return JsonResponse({"ok": True})
    return _redirect_seguro(request, "notifications_list")
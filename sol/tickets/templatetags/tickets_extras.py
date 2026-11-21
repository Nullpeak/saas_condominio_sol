from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def hace_corto(fecha):
    """'ahora', '5 min', '3 h', '2 d' o la fecha, para la bandeja y los mensajes."""
    if not fecha:
        return ""
    segundos = (timezone.now() - fecha).total_seconds()
    if segundos < 60:
        return "ahora"
    if segundos < 3600:
        return f"{int(segundos // 60)} min"
    if segundos < 86400:
        return f"{int(segundos // 3600)} h"
    if segundos < 7 * 86400:
        return f"{int(segundos // 86400)} d"
    return timezone.localtime(fecha).strftime("%d/%m/%y")


@register.filter
def iniciales(usuario):
    if not usuario:
        return "?"
    texto = f"{getattr(usuario, 'nombre', '')[:1]}{getattr(usuario, 'apellido', '')[:1]}".upper()
    return texto or str(usuario)[:1].upper()


@register.simple_tag
def con_param(qs, **cambios):
    """Querystring de la bandeja con algunos parámetros cambiados (vacío = se quita)."""
    from django.http import QueryDict

    params = QueryDict(qs or "", mutable=True)
    params.pop("page", None)
    for clave, valor in cambios.items():
        if valor in ("", None):
            params.pop(clave, None)
        else:
            params[clave] = valor
    texto = params.urlencode()
    return f"?{texto}" if texto else "?"


@register.filter
def color_av(pk):
    """Color estable (0-7) para el avatar sin foto de cada usuario."""
    try:
        return int(pk) % 8
    except (TypeError, ValueError):
        return 0

# Íconos de línea con el mismo trazo que el menú lateral (reemplazan a los emojis)
ICONOS = {
    "actualizar": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "mas": '<path d="M12 5v14M5 12h14"/>',
    "usuario": '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a7 7 0 0 1 14 0v1"/>',
    "reloj": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "check": '<path d="m5 12 4 4 10-10"/>',
    "cerrar": '<path d="M6 6l12 12M18 6 6 18"/>',
    "reabrir": '<path d="M4 11a8 8 0 1 1 2.3 5.7"/><path d="M4 4v7h7"/>',
    "editar": '<path d="m14.7 6.3 3 3M4 20l5.1-1.1L20 8a2.1 2.1 0 0 0-3-3L6.1 15.9 4 20Z"/>',
    "panel": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16"/>',
    "clip": '<path d="m21 11-8.5 8.5a5 5 0 0 1-7-7L14 4a3.3 3.3 0 0 1 4.7 4.7L10.2 17a1.7 1.7 0 0 1-2.4-2.4L15.5 7"/>',
    "enviar": '<path d="M4 12 20 4l-4 16-4-7-8-1Z"/><path d="m12 13 8-9"/>',
    "filtro": '<path d="M4 5h16l-6 8v5l-4 2v-7L4 5Z"/>',
    "lupa": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "candado": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "comentario": '<path d="M4 5h16v11H9l-5 4V5Z"/>',
    "telefono": '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    "correo": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/>',
    "volver": '<path d="M15 18l-6-6 6-6"/>',
    "siguiente": '<path d="m9 18 6-6-6-6"/>',
    "descargar": '<path d="M12 4v11m-5-5 5 5 5-5"/><path d="M5 20h14"/>',
    "video": '<rect x="3" y="6" width="13" height="12" rx="2"/><path d="m16 10 5-3v10l-5-3"/>',
    "documento": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z"/><path d="M14 3v5h5M9 13h6m-6 4h6"/>',
}


@register.simple_tag
def icono(nombre, tam=16):
    from django.utils.html import format_html, mark_safe
    trazo = ICONOS.get(nombre, "")
    return format_html(
        '<svg class="tkx-ico" width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{}</svg>',
        tam, tam, mark_safe(trazo),
    )

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

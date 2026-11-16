from django import template

register = template.Library()


@register.filter
def getlist(data, key):
    try:
        return data.getlist(key)
    except (AttributeError, TypeError):
        if isinstance(data, dict):
            value = data.get(key, [])
            return value if isinstance(value, (list, tuple)) else [value] if value else []
        return []

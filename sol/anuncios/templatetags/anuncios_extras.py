from django import template

register = template.Library()


@register.filter
def getlist(data, key):
    try:
        return data.getlist(key)
    except (AttributeError, TypeError):
        return []

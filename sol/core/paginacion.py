from django.core.paginator import Paginator


def paginar(request, objetos, por_pagina=25, parametro="page"):
    """Paginate a queryset while preserving the active filters in the URL."""
    pagina = Paginator(objetos, por_pagina).get_page(request.GET.get(parametro))
    parametros = request.GET.copy()
    parametros.pop(parametro, None)
    return {
        "page_obj": pagina,
        "page_range": pagina.paginator.get_elided_page_range(pagina.number),
        "querystring": parametros.urlencode(),
    }

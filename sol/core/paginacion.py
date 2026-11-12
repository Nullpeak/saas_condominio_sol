from django.core.paginator import Paginator


def paginar(request, objetos, por_pagina=25):
    """Paginate a queryset while preserving the active filters in the URL."""
    pagina = Paginator(objetos, por_pagina).get_page(request.GET.get("page"))
    parametros = request.GET.copy()
    parametros.pop("page", None)
    return {
        "page_obj": pagina,
        "page_range": pagina.paginator.get_elided_page_range(pagina.number),
        "querystring": parametros.urlencode(),
    }

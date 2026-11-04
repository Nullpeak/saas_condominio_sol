from django.db.models import Q

from core.models import Unidad
from .models import AnuncioOficial, AnuncioResidente, LecturaAnuncioOficial, LecturaAnuncioResidente


def anuncios_nuevos(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"anuncios_nuevos": False}
    try:
        perfil = user.usuario
    except Exception:
        return {"anuncios_nuevos": False}

    oficiales = AnuncioOficial.objects.all()
    residentes = AnuncioResidente.objects.all()
    if perfil.rol in ("Super Administrador", "Administrador", "Gestor"):
        condominios = perfil.condominios_asignados()
        oficiales = oficiales.filter(condominio__in=condominios)
        residentes = residentes.filter(condominio__in=condominios)
    elif perfil.rol == "Residente":
        unidades = Unidad.objects.filter(Q(dueno=perfil) | Q(arrendatario=perfil)).distinct()
        unidad_ids = list(unidades.values_list("id", flat=True))
        edificio_ids = list(unidades.values_list("edificio_id", flat=True))
        condo_ids = list(unidades.values_list("edificio__condominio_id", flat=True))
        oficiales = oficiales.filter(condominio_id__in=condo_ids).filter(
            Q(alcance=AnuncioOficial.Alcance.TODOS)
            | Q(alcance=AnuncioOficial.Alcance.EDIFICIOS, edificios__id__in=edificio_ids)
            | Q(alcance=AnuncioOficial.Alcance.UNIDADES, unidades__id__in=unidad_ids)
        ).distinct()
        residentes = residentes.filter(
            Q(estado=AnuncioResidente.Estado.PUBLICADO, alcance=AnuncioResidente.Alcance.CONDOMINIO, condominio_id__in=condo_ids)
            | Q(estado=AnuncioResidente.Estado.PUBLICADO, alcance=AnuncioResidente.Alcance.EDIFICIO, unidad__edificio_id__in=edificio_ids)
            | Q(autor=perfil, estado=AnuncioResidente.Estado.PENDIENTE)
        ).distinct()
    else:
        return {"anuncios_nuevos": False}

    nuevos_oficiales = oficiales.exclude(pk__in=LecturaAnuncioOficial.objects.filter(usuario=perfil).values("anuncio_id")).exists()
    nuevos_residentes = residentes.exclude(pk__in=LecturaAnuncioResidente.objects.filter(usuario=perfil).values("anuncio_id")).exists()
    return {"anuncios_nuevos": nuevos_oficiales or nuevos_residentes}

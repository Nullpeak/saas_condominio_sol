from datetime import date, timedelta
import mimetypes

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.urls import reverse

from core.models import Condominio, Edificio, Unidad
from .models import (
    AnuncioOficial,
    AnuncioResidente,
    LecturaAnuncioOficial,
    LecturaAnuncioResidente,
)


ROLES_STAFF = ("Super Administrador", "Administrador", "Gestor")


def _perfil(request):
    return request.user.usuario


def _staff(perfil):
    return perfil.rol in ROLES_STAFF


def _oficiales_visibles(perfil):
    qs = AnuncioOficial.objects.select_related("condominio", "autor").prefetch_related("edificios", "unidades")
    if perfil.rol == "Super Administrador":
        return qs
    if perfil.rol in ("Administrador", "Gestor"):
        condominios = perfil.condominios_asignados()
        return qs.filter(condominio__in=condominios) if condominios.exists() else qs.none()
    if perfil.rol != "Residente":
        return qs.none()
    mis_unidades = Unidad.objects.filter(Q(dueno=perfil) | Q(arrendatario=perfil)).distinct()
    return qs.filter(condominio_id__in=mis_unidades.values("edificio__condominio_id")).filter(
        Q(alcance=AnuncioOficial.Alcance.TODOS)
        | Q(alcance=AnuncioOficial.Alcance.EDIFICIOS, edificios__unidades__in=mis_unidades)
        | Q(alcance=AnuncioOficial.Alcance.UNIDADES, unidades__in=mis_unidades)
    ).distinct()


def _anuncios_residentes_visibles(perfil):
    qs = AnuncioResidente.objects.select_related(
        "condominio", "autor", "unidad", "unidad__edificio", "moderado_por"
    )
    if _staff(perfil):
        if perfil.rol == "Super Administrador":
            return qs
        condominios = perfil.condominios_asignados()
        return qs.filter(condominio__in=condominios) if condominios.exists() else qs.none()
    if perfil.rol != "Residente":
        return qs.none()
    mis_unidades = Unidad.objects.filter(Q(dueno=perfil) | Q(arrendatario=perfil)).distinct()
    return qs.filter(
        Q(estado=AnuncioResidente.Estado.PUBLICADO, alcance=AnuncioResidente.Alcance.CONDOMINIO,
          condominio_id__in=mis_unidades.values("edificio__condominio_id"))
        | Q(estado=AnuncioResidente.Estado.PUBLICADO, alcance=AnuncioResidente.Alcance.EDIFICIO,
            unidad__edificio_id__in=mis_unidades.values("edificio_id"))
        | Q(autor=perfil)
    ).distinct()


def _marca_como_leidos(perfil, tipo):
    if tipo == "oficial":
        filas = [LecturaAnuncioOficial(usuario=perfil, anuncio_id=pk)
                 for pk in _oficiales_visibles(perfil).values_list("id", flat=True)]
        LecturaAnuncioOficial.objects.bulk_create(filas, ignore_conflicts=True)
    else:
        filas = [LecturaAnuncioResidente(usuario=perfil, anuncio_id=pk)
                 for pk in _anuncios_residentes_visibles(perfil).values_list("id", flat=True)]
        LecturaAnuncioResidente.objects.bulk_create(filas, ignore_conflicts=True)


def _agrupar_por_dia(anuncios):
    grupos = []
    hoy = timezone.localdate()
    ayer = hoy - timedelta(days=1)
    for anuncio in anuncios:
        dia = timezone.localtime(anuncio.creado).date()
        if not grupos or grupos[-1]["dia"] != dia:
            if dia == hoy:
                etiqueta = "Hoy"
            elif dia == ayer:
                etiqueta = "Ayer"
            else:
                etiqueta = dia.strftime("%d/%m/%Y")
            grupos.append({"dia": dia, "etiqueta": etiqueta, "anuncios": []})
        grupos[-1]["anuncios"].append(anuncio)
    return grupos


@login_required
def lista(request):
    perfil = _perfil(request)
    if perfil.rol not in (*ROLES_STAFF, "Residente"):
        return redirect("inicio")
    tipo = request.GET.get("tipo", "oficial")
    if tipo not in ("oficial", "residentes"):
        tipo = "oficial"
    _marca_como_leidos(perfil, tipo)
    residentes_visibles = _anuncios_residentes_visibles(perfil)
    nuevos_residentes = residentes_visibles.exclude(
        pk__in=LecturaAnuncioResidente.objects.filter(usuario=perfil).values("anuncio_id")
    ).count()
    anuncios = _oficiales_visibles(perfil) if tipo == "oficial" else _anuncios_residentes_visibles(perfil)
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        anuncios = anuncios.filter(Q(titulo__icontains=busqueda) | Q(descripcion__icontains=busqueda))
    desde = request.GET.get("desde", "").strip()
    hasta = request.GET.get("hasta", "").strip()
    if _staff(perfil) and desde:
        try:
            anuncios = anuncios.filter(creado__date__gte=date.fromisoformat(desde))
        except ValueError:
            desde = ""
    if _staff(perfil) and hasta:
        try:
            anuncios = anuncios.filter(creado__date__lte=date.fromisoformat(hasta))
        except ValueError:
            hasta = ""
    estado_filtro = request.GET.get("estado", "")
    if tipo == "residentes" and _staff(perfil) and estado_filtro in AnuncioResidente.Estado.values:
        anuncios = anuncios.filter(estado=estado_filtro)
    grupos = _agrupar_por_dia(list(anuncios.order_by("-creado", "-id")))
    return render(request, "anuncios/lista.html", {
        "perfil": perfil,
        "tipo": tipo,
        "grupos": grupos,
        "busqueda": busqueda,
        "desde": desde,
        "hasta": hasta,
        "estado_filtro": estado_filtro,
        "estados": AnuncioResidente.Estado.choices,
        "pendientes_revision": nuevos_residentes,
        "puede_publicar_oficial": perfil.rol in ROLES_STAFF,
        "puede_crear_residente": perfil.rol == "Residente",
        "puede_moderar": perfil.rol in ROLES_STAFF,
    })


@login_required
def crear(request):
    perfil = _perfil(request)
    tipo = request.GET.get("tipo", request.POST.get("tipo", "oficial"))
    if tipo not in ("oficial", "residentes"):
        tipo = "oficial"
    residente = tipo == "residentes"
    if (residente and perfil.rol != "Residente") or (not residente and perfil.rol not in ROLES_STAFF):
        messages.error(request, "No tienes permiso para crear este tipo de anuncio.")
        return redirect("anuncios:lista")

    mis_unidades = Unidad.objects.select_related("edificio", "edificio__condominio").filter(
        Q(dueno=perfil) | Q(arrendatario=perfil)
    ).distinct() if residente else Unidad.objects.none()
    condominios = perfil.condominios_asignados().filter(activo=True).order_by("nombre")
    errores = []
    if request.method == "POST":
        titulo = request.POST.get("titulo", "").strip()
        descripcion = request.POST.get("descripcion", "").strip()
        alcance = request.POST.get("alcance", "")
        imagen = request.FILES.get("imagen")
        if perfil.rol == "Super Administrador":
            try:
                condominio_id = int(request.POST.get("condominio", ""))
            except (TypeError, ValueError):
                condominio_id = None
            condominio = Condominio.objects.filter(pk=condominio_id).first() if condominio_id else None
        else:
            try:
                condominio_id = int(request.POST.get("condominio", ""))
            except (TypeError, ValueError):
                condominio_id = None
            condominio = condominios.filter(pk=condominio_id).first() if condominio_id else None
            if condominio is None and condominios.count() == 1:
                condominio = condominios.first()
        if not condominio:
            errores.append("Selecciona un condominio válido.")
        if not titulo:
            errores.append("Escribe un título para el anuncio.")
        elif len(titulo) > 150:
            errores.append("El título no puede superar 150 caracteres.")
        if not descripcion:
            errores.append("Escribe una descripción.")

        unidad = None
        edificios = []
        unidades = []
        if residente:
            try:
                unidad_id = int(request.POST.get("unidad", ""))
            except (TypeError, ValueError):
                unidad_id = None
            unidad = mis_unidades.filter(pk=unidad_id).first() if unidad_id else None
            if not unidad:
                errores.append("Selecciona una de tus unidades.")
            if alcance not in AnuncioResidente.Alcance.values:
                errores.append("Selecciona quién podrá ver el anuncio.")
        else:
            if alcance not in AnuncioOficial.Alcance.values:
                errores.append("Selecciona el alcance del anuncio.")
            if condominio:
                if alcance == AnuncioOficial.Alcance.EDIFICIOS:
                    edificios = list(Edificio.objects.filter(condominio=condominio, pk__in=request.POST.getlist("edificios")))
                    if not edificios:
                        errores.append("Selecciona al menos un edificio.")
                elif alcance == AnuncioOficial.Alcance.UNIDADES:
                    unidades = list(Unidad.objects.filter(edificio__condominio=condominio, pk__in=request.POST.getlist("unidades")))
                    if not unidades:
                        errores.append("Selecciona al menos una unidad.")
        if imagen:
            try:
                for validador in (AnuncioOficial._meta.get_field("imagen").validators if not residente else AnuncioResidente._meta.get_field("imagen").validators):
                    validador(imagen)
            except ValidationError as error:
                errores.extend(error.messages)

        if not errores:
            if residente:
                anuncio = AnuncioResidente(
                    condominio=unidad.edificio.condominio,
                    autor=perfil,
                    unidad=unidad,
                    titulo=titulo,
                    descripcion=descripcion,
                    imagen=imagen or "",
                    alcance=alcance,
                    estado=AnuncioResidente.Estado.PENDIENTE,
                )
                anuncio.full_clean()
                anuncio.save()
                messages.success(request, "Tu anuncio quedó pendiente de revisión del Gestor o Administrador.")
            else:
                anuncio = AnuncioOficial(
                    condominio=condominio,
                    autor=perfil,
                    titulo=titulo,
                    descripcion=descripcion,
                    imagen=imagen or "",
                    alcance=alcance,
                )
                anuncio.full_clean()
                anuncio.save()
                anuncio.edificios.set(edificios)
                anuncio.unidades.set(unidades)
                messages.success(request, "El anuncio oficial se publicó correctamente.")
            return redirect("anuncios:lista")

    edificios_disponibles = Edificio.objects.filter(condominio__in=condominios).order_by("nombre")
    unidades_disponibles = Unidad.objects.filter(edificio__condominio__in=condominios).select_related("edificio").order_by("edificio__nombre", "numero_unidad")
    return render(request, "anuncios/crear.html", {
        "perfil": perfil,
        "tipo": tipo,
        "es_residente": residente,
        "unidades": mis_unidades,
        "condominios": condominios,
        "edificios": edificios_disponibles,
        "unidades_disponibles": unidades_disponibles,
        "errores": errores,
        "datos": request.POST if request.method == "POST" else {},
        "unidad_unica": mis_unidades.count() == 1,
    })


@login_required
def moderar(request, anuncio_id, accion):
    perfil = _perfil(request)
    if request.method != "POST" or perfil.rol not in ROLES_STAFF or accion not in ("publicar", "rechazar"):
        return redirect("anuncios:lista")
    anuncios = AnuncioResidente.objects.all() if perfil.rol == "Super Administrador" else AnuncioResidente.objects.filter(condominio__in=perfil.condominios_asignados())
    anuncio = get_object_or_404(anuncios, pk=anuncio_id)
    anuncio.estado = AnuncioResidente.Estado.PUBLICADO if accion == "publicar" else AnuncioResidente.Estado.RECHAZADO
    anuncio.moderado_por = perfil
    anuncio.moderado = timezone.now()
    anuncio.save(update_fields=["estado", "moderado_por", "moderado"])
    messages.success(request, "El anuncio se publicó." if accion == "publicar" else "El anuncio fue rechazado.")
    return redirect(f"{reverse('anuncios:lista')}?tipo=residentes")


@login_required
def imagen(request, tipo, anuncio_id):
    perfil = _perfil(request)
    if tipo == "oficial":
        anuncio = get_object_or_404(_oficiales_visibles(perfil), pk=anuncio_id)
    elif tipo == "residentes":
        anuncio = get_object_or_404(_anuncios_residentes_visibles(perfil), pk=anuncio_id)
    else:
        return redirect("anuncios:lista")
    if not anuncio.imagen:
        return redirect("anuncios:lista")
    content_type = mimetypes.guess_type(anuncio.imagen.name)[0] or "application/octet-stream"
    return FileResponse(anuncio.imagen.open("rb"), content_type=content_type)

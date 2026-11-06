from datetime import date
from functools import wraps
from itertools import chain

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.models import Unidad

from . import services
from .forms import ComentarioForm, EditarTicketForm, GestionForm, TicketForm, VisitaFormSet
from .models import Ticket, TicketArchivo, TicketVisita
from .permisos import (
    ACCIONES_RESIDENTE, Rol, acciones_residente, es_staff,
    puede_comentar, puede_editar, puede_gestionar, tickets_visibles,
)

POR_PAGINA = 20

TEXTO_ACCION = {
    "confirmar": "Gracias por confirmar. El ticket quedó cerrado.",
    "reabrir": "Reabriste el ticket. El equipo lo revisará de nuevo.",
    "cancelar": "Cerraste el ticket.",
}


def con_perfil(view):
    """login + perfil activo. Deja el perfil en request.perfil."""
    @login_required
    @wraps(view)
    def envoltura(request, *args, **kwargs):
        perfil = getattr(request.user, "usuario", None)
        if perfil is None or not perfil.activo:
            raise PermissionDenied
        request.perfil = perfil
        return view(request, *args, **kwargs)
    return envoltura


def _ticket_visible(perfil, ticket_id):
    return get_object_or_404(
        tickets_visibles(perfil).select_related(
            "usuario", "asignado", "unidad__edificio", "condominio"
        ),
        pk=ticket_id,
    )


def _unidades_filtro(perfil, condominio_id=""):
    """Unidades que se ofrecen en el filtro de la lista."""
    unidades = Unidad.objects.select_related("edificio__condominio").order_by(
        "edificio__condominio__nombre", "edificio__nombre", "numero_unidad"
    )
    if perfil.rol == Rol.RESIDENTE:
        return unidades.filter(Q(dueno=perfil) | Q(arrendatario=perfil)).distinct()
    if perfil.rol != Rol.SUPER:
        unidades = unidades.filter(edificio__condominio__in=perfil.condominios_asignados())
    if condominio_id.isdigit():
        unidades = unidades.filter(edificio__condominio_id=condominio_id)
    return unidades


# ============================================================== LISTA
@con_perfil
def lista(request):
    perfil = request.perfil
    staff = es_staff(perfil)
    base = tickets_visibles(perfil)

    # --- filtros ---
    f = {
        "estado": request.GET.get("estado", "abiertos"),
        "prioridad": request.GET.get("prioridad", ""),
        "categoria": request.GET.get("categoria", ""),
        "asignado": request.GET.get("asignado", ""),
        "condominio": request.GET.get("condominio", ""),
        "unidad": request.GET.get("unidad", ""),
        "desde": request.GET.get("desde", ""),
        "hasta": request.GET.get("hasta", ""),
        "q": request.GET.get("q", "").strip(),
    }
    # Selector de condominio: super y administradores con más de un condominio
    condominios = None
    if staff:
        asignados = perfil.condominios_asignados().filter(activo=True).order_by("nombre")
        if asignados.count() > 1:
            condominios = asignados
    if condominios is not None and f["condominio"].isdigit():
        base = base.filter(condominio_id=f["condominio"])

    unidades = _unidades_filtro(perfil, f["condominio"] if condominios is not None else "")
    if f["unidad"].isdigit() and unidades.filter(pk=f["unidad"]).exists():
        base = base.filter(unidad_id=f["unidad"])
    else:
        f["unidad"] = ""
    for campo, lookup in (("desde", "creado__date__gte"), ("hasta", "creado__date__lte")):
        try:
            base = base.filter(**{lookup: date.fromisoformat(f[campo])}) if f[campo] else base
        except ValueError:
            f[campo] = ""

    conteos = dict(base.values_list("estado").annotate(n=Count("id")).values_list("estado", "n"))
    qs = base
    if f["estado"] == "abiertos":
        qs = qs.filter(estado__in=Ticket.ABIERTOS)
    elif f["estado"] in Ticket.Estado.values:
        qs = qs.filter(estado=f["estado"])
    if f["prioridad"] == "sin":
        qs = qs.filter(prioridad="")
    elif f["prioridad"] in Ticket.Prioridad.values:
        qs = qs.filter(prioridad=f["prioridad"])
    if f["categoria"] in Ticket.Categoria.values:
        qs = qs.filter(categoria=f["categoria"])
    if staff and f["asignado"] == "yo":
        qs = qs.filter(asignado=perfil)
    elif staff and f["asignado"] == "nadie":
        qs = qs.filter(asignado__isnull=True)
    if f["q"]:
        termino = f["q"].lstrip("#")
        filtro = Q(titulo__icontains=termino) | Q(descripcion__icontains=termino)
        if termino.isdigit():
            filtro |= Q(pk=int(termino))
        qs = qs.filter(filtro)

    page = Paginator(
        qs.select_related("usuario", "asignado", "unidad__edificio", "condominio")
        .annotate(n_comentarios=Count("comentarios", filter=Q(comentarios__interno=False) if not staff else None))
        .order_by("-actualizado", "-pk"),  # con annotate Django no aplica el ordering del Meta
        POR_PAGINA,
    ).get_page(request.GET.get("page"))

    # Querystring sin "page", para la paginación
    params = request.GET.copy()
    params.pop("page", None)

    pestanas = [("abiertos", "Pendientes", sum(conteos.get(e, 0) for e in Ticket.ABIERTOS))]
    pestanas += [(v, l, conteos.get(v, 0)) for v, l in Ticket.Estado.choices]
    pestanas.append(("todos", "Todos", sum(conteos.values())))

    return render(request, "tickets/lista.html", {
        "page": page, "f": f, "staff": staff, "pestanas": pestanas,
        "querystring": params.urlencode(),
        "prioridades": Ticket.Prioridad.choices, "categorias": Ticket.Categoria.choices,
        "condominios": condominios,
        "unidades": unidades if staff or unidades.count() > 1 else None,
        "sin_asignar": base.filter(estado__in=Ticket.ABIERTOS, asignado__isnull=True).count() if staff else 0,
    })


# ============================================================== CREAR
@con_perfil
def crear(request):
    perfil = request.perfil
    inicial = {"categoria": request.GET["categoria"]} if request.GET.get("categoria") in Ticket.Categoria.values else None
    form = TicketForm(request.POST or None, request.FILES or None, perfil=perfil, initial=inicial)
    visitas = VisitaFormSet(request.POST or None, prefix="visitas")
    # El formulario de visitas solo cuenta si el ticket es de esa categoría
    visitas_ok = not form.es_de_visitas() or visitas.is_valid()
    if request.method == "POST" and form.is_valid() and visitas_ok:
        d = form.cleaned_data
        ticket = services.crear_ticket(
            perfil,
            condominio=d["condominio_final"],
            titulo=d["titulo"],
            descripcion=d["descripcion"],
            categoria=d["categoria"],
            unidad=d.get("unidad"),
            archivos=d["archivos"],
            visitas=[f.cleaned_data for f in visitas] if form.es_de_visitas() else None,
        )
        if form.es_de_visitas():
            messages.success(request, f"Solicitud #{ticket.pk} enviada. Las visitas quedan registradas cuando la administración las apruebe.")
        else:
            messages.success(request, f"Ticket #{ticket.pk} creado. Te avisaremos cuando haya novedades.")
        return redirect("tickets:detalle", ticket.pk)
    return render(request, "tickets/crear.html", {"form": form, "visitas": visitas})


# ============================================================== DETALLE
def _contexto_detalle(perfil, ticket, comentario_form=None, gestion_form=None):
    gestiona = puede_gestionar(perfil, ticket)

    comentarios = ticket.comentarios.select_related("autor").prefetch_related("archivos")
    if not gestiona:
        comentarios = comentarios.filter(interno=False)
    eventos = ticket.eventos.select_related("autor")
    # Una sola línea de tiempo: mensajes e historial ordenados por fecha
    linea = sorted(
        chain(
            ({"tipo": "comentario", "obj": c, "creado": c.creado} for c in comentarios),
            ({"tipo": "evento", "obj": e, "creado": e.creado} for e in eventos),
        ),
        key=lambda x: x["creado"],
    )

    if comentario_form is None and puede_comentar(perfil, ticket):
        comentario_form = ComentarioForm(staff=gestiona)
    if gestion_form is None and gestiona:
        gestion_form = GestionForm(perfil=perfil, ticket=ticket)

    visitas = list(ticket.visitas.select_related("revisada_por", "visita"))
    return {
        "ticket": ticket,
        "visitas": visitas,
        "visitas_pendientes": sum(v.estado == TicketVisita.Estado.PENDIENTE for v in visitas),
        "puede_revisar_visitas": gestiona and ticket.estado != Ticket.Estado.CERRADO,
        "adjuntos": ticket.pruebas.filter(comentario__isnull=True),
        "linea": linea,
        "gestiona": gestiona,
        "comentario_form": comentario_form if puede_comentar(perfil, ticket) else None,
        "gestion_form": gestion_form,
        "acciones": acciones_residente(perfil, ticket),
        "puede_editar": puede_editar(perfil, ticket),
    }


@con_perfil
def detalle(request, ticket_id):
    ticket = _ticket_visible(request.perfil, ticket_id)
    return render(request, "tickets/detalle.html", _contexto_detalle(request.perfil, ticket))


@con_perfil
def editar(request, ticket_id):
    """El residente corrige su ticket mientras sigue abierto (nadie lo ha tomado)."""
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if not puede_editar(perfil, ticket):
        messages.error(request, "Solo puedes editar tus tickets que siguen abiertos.")
        return redirect("tickets:detalle", ticket.pk)
    # instance=copia: si el formulario falla no se ensucia el ticket que muestra la plantilla
    form = EditarTicketForm(request.POST or None, instance=Ticket.objects.get(pk=ticket.pk))
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        cambio = services.editar(
            perfil, ticket,
            titulo=d["titulo"], descripcion=d["descripcion"],
            categoria=d.get("categoria", ticket.categoria),
        )
        messages.success(request, "Ticket actualizado." if cambio else "No hubo cambios.")
        return redirect("tickets:detalle", ticket.pk)
    return render(request, "tickets/editar.html", {"form": form, "ticket": ticket})


@con_perfil
@require_POST
def comentar(request, ticket_id):
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if not puede_comentar(perfil, ticket):
        raise PermissionDenied
    gestiona = puede_gestionar(perfil, ticket)
    form = ComentarioForm(request.POST, request.FILES, staff=gestiona)
    if not form.is_valid():
        return render(request, "tickets/detalle.html",
                      _contexto_detalle(perfil, ticket, comentario_form=form), status=400)
    services.comentar(
        perfil, ticket,
        texto=form.cleaned_data["texto"],
        interno=form.cleaned_data.get("interno", False),
        archivos=form.cleaned_data["archivos"],
        es_staff=gestiona,
    )
    messages.success(request, "Mensaje enviado.")
    return redirect("tickets:detalle", ticket.pk)


@con_perfil
@require_POST
def gestionar(request, ticket_id):
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if not puede_gestionar(perfil, ticket):
        raise PermissionDenied
    form = GestionForm(request.POST, perfil=perfil, ticket=ticket)
    if not form.is_valid():
        return render(request, "tickets/detalle.html",
                      _contexto_detalle(perfil, ticket, gestion_form=form), status=400)
    try:
        eventos = services.actualizar(perfil, ticket, **form.cleaned_data)
    except ValidationError as e:
        form.add_error(None, e)
        return render(request, "tickets/detalle.html",
                      _contexto_detalle(perfil, ticket, gestion_form=form), status=400)
    if eventos:
        messages.success(request, "Ticket actualizado.")
    else:
        messages.info(request, "No hubo cambios.")
    return redirect("tickets:detalle", ticket.pk)


@con_perfil
@require_POST
def accion(request, ticket_id, nombre):
    """Botones del residente: confirmar solución, reabrir o cancelar."""
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if nombre not in acciones_residente(perfil, ticket):
        raise PermissionDenied
    _, destino = ACCIONES_RESIDENTE[nombre]
    services.actualizar(perfil, ticket, estado=destino)
    motivo = request.POST.get("motivo", "").strip()
    if motivo:
        services.comentar(perfil, Ticket.objects.get(pk=ticket.pk), texto=motivo)
    messages.success(request, TEXTO_ACCION[nombre])
    return redirect("tickets:detalle", ticket.pk)


# ============================================================== VISITAS
@con_perfil
@require_POST
def revisar_visita(request, ticket_id, visita_id, decision):
    """El staff aprueba o rechaza una visita (o todas las pendientes, con visita_id = 0)."""
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if not puede_gestionar(perfil, ticket) or decision not in ("aprobar", "rechazar"):
        raise PermissionDenied
    pendientes = ticket.visitas.filter(estado=TicketVisita.Estado.PENDIENTE)
    if visita_id:
        pendientes = pendientes.filter(pk=visita_id)
    if not pendientes.exists():
        messages.info(request, "No hay visitas pendientes que revisar.")
        return redirect("tickets:detalle", ticket.pk)

    hechas = 0
    for visita in pendientes:
        try:
            if decision == "aprobar":
                services.aprobar_visita(perfil, visita)
            else:
                services.rechazar_visita(perfil, visita, request.POST.get("motivo", "").strip())
            hechas += 1
        except ValidationError as e:
            messages.error(request, f"{visita.nombre}: {' '.join(e.messages)}")
    if hechas:
        verbo = "aprobada" if decision == "aprobar" else "rechazada"
        messages.success(request, f"{hechas} visita{'s' if hechas > 1 else ''} {verbo}{'s' if hechas > 1 else ''}.")
    return redirect("tickets:detalle", ticket.pk)


# ============================================================== ARCHIVOS
@con_perfil
def archivo(request, archivo_id):
    """Los adjuntos no se sirven desde /media: solo los descarga quien puede ver el ticket."""
    perfil = request.perfil
    adj = get_object_or_404(TicketArchivo.objects.select_related("ticket", "comentario"), pk=archivo_id)
    if not tickets_visibles(perfil).filter(pk=adj.ticket_id).exists():
        raise Http404
    if adj.comentario_id and adj.comentario.interno and not puede_gestionar(perfil, adj.ticket):
        raise Http404
    try:
        f = adj.archivo.open("rb")
    except FileNotFoundError:
        raise Http404
    descargar = not adj.es_imagen or "descargar" in request.GET
    return FileResponse(f, as_attachment=descargar, filename=adj.nombre or None)

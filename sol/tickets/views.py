"""
Tickets con vista de bandeja tipo chat: a la izquierda la lista (carpetas, filtros y búsqueda),
al centro la conversación del ticket elegido y a la derecha sus datos y la gestión.

La lista y el detalle usan la misma plantilla (tickets/bandeja.html). Los filtros viajan en la
querystring y los formularios la devuelven en el campo oculto "qs", para que después de responder
o cambiar el estado se vuelva al mismo lugar de la bandeja.
"""
from datetime import date
from functools import wraps
from itertools import chain

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import Unidad

from . import services
from .forms import ComentarioForm, EditarTicketForm, GestionForm, TicketForm
from .models import Ticket, TicketArchivo
from .permisos import (
    ACCIONES_RESIDENTE, Rol, acciones_residente, asignables, es_staff, estados_siguientes,
    puede_comentar, puede_editar, puede_gestionar, tickets_visibles,
)

POR_PAGINA = 30
E = Ticket.Estado

TEXTO_ACCION = {
    "confirmar": "Gracias por confirmar. El ticket quedó cerrado.",
    "reabrir": "Reabriste el ticket. El equipo lo revisará de nuevo.",
    "cancelar": "Cerraste el ticket.",
}

# Carpetas de la bandeja: (clave, nombre, filtro). El filtro recibe el perfil.
CARPETAS_STAFF = [
    ("pendientes", "Por atender", lambda p: Q(estado__in=Ticket.ABIERTOS) & ~Q(estado=E.ESPERANDO)),
    ("sin_asignar", "Sin asignar", lambda p: Q(estado__in=Ticket.ABIERTOS, asignado__isnull=True)),
    ("mios", "Mis tickets", lambda p: Q(estado__in=Ticket.ABIERTOS, asignado=p)),
    ("esperando", "Esperando residente", lambda p: Q(estado=E.ESPERANDO)),
    ("resueltos", "Resueltos", lambda p: Q(estado=E.RESUELTO)),
    ("cerrados", "Cerrados", lambda p: Q(estado=E.CERRADO)),
    ("todos", "Todos", lambda p: Q()),
]
CARPETAS_RESIDENTE = [
    ("pendientes", "Activos", lambda p: Q(estado__in=Ticket.ABIERTOS + (E.RESUELTO,))),
    ("cerrados", "Cerrados", lambda p: Q(estado=E.CERRADO)),
    ("todos", "Todos", lambda p: Q()),
]

# Botones rápidos del encabezado para el staff: estado destino -> texto del botón
BOTON_ESTADO = {
    E.EN_REVISION: "Tomar",
    E.ESPERANDO: "Pedir respuesta",
    E.RESUELTO: "Marcar resuelto",
    E.CERRADO: "Cerrar",
    E.ABIERTO: "Reabrir",
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


def _qs_limpia(texto):
    """Querystring de la bandeja (sin page) re-codificada: nunca se usa tal cual llega."""
    params = QueryDict(texto or "", mutable=True)
    params.pop("page", None)
    params.pop("qs", None)
    return params.urlencode()


def _volver(request, ticket):
    """Redirige al ticket manteniendo los filtros con que se estaba mirando la bandeja."""
    url = reverse("tickets:detalle", args=[ticket.pk])
    qs = _qs_limpia(request.POST.get("qs", ""))
    return redirect(f"{url}?{qs}" if qs else url)


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


# ============================================================== BANDEJA
def _bandeja(request, perfil):
    """Lista de la izquierda: carpetas con contadores, filtros y la página de tickets."""
    staff = es_staff(perfil)
    carpetas_def = CARPETAS_STAFF if staff else CARPETAS_RESIDENTE
    claves = [c[0] for c in carpetas_def]
    base = tickets_visibles(perfil)

    g = request.GET
    f = {
        "carpeta": g.get("carpeta", ""),
        "categoria": g.get("categoria", ""),
        "prioridad": g.get("prioridad", ""),
        "condominio": g.get("condominio", ""),
        "unidad": g.get("unidad", ""),
        "desde": g.get("desde", ""),
        "hasta": g.get("hasta", ""),
        "q": g.get("q", "").strip(),
    }
    if f["carpeta"] not in claves:
        # Compatibilidad con los enlaces antiguos (?estado=todos)
        f["carpeta"] = "todos" if g.get("estado") == "todos" else "pendientes"

    # --- filtros que afectan a todas las carpetas (y a sus contadores) ---
    condominios = None
    if staff:
        asignados = perfil.condominios_asignados().filter(activo=True).order_by("nombre")
        if asignados.count() > 1:
            condominios = asignados
    if condominios is not None and f["condominio"].isdigit():
        base = base.filter(condominio_id=f["condominio"])
    else:
        f["condominio"] = ""

    unidades = _unidades_filtro(perfil, f["condominio"])
    if f["unidad"].isdigit() and unidades.filter(pk=f["unidad"]).exists():
        base = base.filter(unidad_id=f["unidad"])
    else:
        f["unidad"] = ""
    for campo, lookup in (("desde", "creado__date__gte"), ("hasta", "creado__date__lte")):
        try:
            base = base.filter(**{lookup: date.fromisoformat(f[campo])}) if f[campo] else base
        except ValueError:
            f[campo] = ""
    if f["prioridad"] == "sin":
        base = base.filter(prioridad="")
    elif f["prioridad"] in Ticket.Prioridad.values:
        base = base.filter(prioridad=f["prioridad"])
    else:
        f["prioridad"] = ""
    if f["q"]:
        termino = f["q"].lstrip("#")
        filtro = (Q(titulo__icontains=termino) | Q(descripcion__icontains=termino)
                  | Q(usuario__nombre__icontains=termino) | Q(usuario__apellido__icontains=termino)
                  | Q(unidad__numero_unidad__iexact=termino))
        if termino.isdigit():
            filtro |= Q(pk=int(termino))
        base = base.filter(filtro)

    # Contadores de carpeta: respetan los filtros de arriba, no la categoría
    conteos = base.aggregate(**{clave: Count("id", filter=fn(perfil)) for clave, _, fn in carpetas_def})
    carpetas = [(clave, nombre, conteos[clave]) for clave, nombre, _ in carpetas_def]

    qs = base.filter(dict((c[0], c[2]) for c in carpetas_def)[f["carpeta"]](perfil))
    conteo_categoria = dict(qs.values_list("categoria").annotate(n=Count("id")).values_list("categoria", "n"))
    if f["categoria"] in Ticket.Categoria.values:
        qs = qs.filter(categoria=f["categoria"])
    else:
        f["categoria"] = ""

    comentarios_visibles = None if staff else Q(comentarios__interno=False)
    page = Paginator(
        qs.select_related("usuario", "asignado", "unidad__edificio", "condominio")
        .annotate(n_comentarios=Count("comentarios", filter=comentarios_visibles))
        .order_by("-actualizado", "-pk"),  # con annotate Django no aplica el ordering del Meta
        POR_PAGINA,
    ).get_page(g.get("page"))

    return {
        "staff": staff,
        "f": f,
        "carpetas": carpetas,
        "carpeta_nombre": dict((c[0], c[1]) for c in carpetas)[f["carpeta"]],
        "categorias": [(v, l, conteo_categoria.get(v, 0)) for v, l in Ticket.Categoria.choices],
        "prioridades": Ticket.Prioridad.choices,
        "condominios": condominios,
        "unidades": unidades if staff or unidades.count() > 1 else None,
        "filtros_extra": any(f[k] for k in ("prioridad", "condominio", "unidad", "desde", "hasta")),
        "page": page,
        "qs": _qs_limpia(request.GET.urlencode()),
    }


def _contexto_ticket(perfil, ticket, comentario_form=None, gestion_form=None):
    """Conversación del centro y panel de la derecha."""
    gestiona = puede_gestionar(perfil, ticket)

    comentarios = ticket.comentarios.select_related("autor").prefetch_related("archivos")
    if not gestiona:
        comentarios = comentarios.filter(interno=False)
    eventos = ticket.eventos.select_related("autor").exclude(tipo="creado")
    # Una sola conversación: mensajes y cambios ordenados por fecha
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

    botones_estado = []
    puede_asignarse = False
    otros = []
    if gestiona:
        siguientes = estados_siguientes(perfil, ticket)
        botones_estado = [(e, BOTON_ESTADO[e]) for e in BOTON_ESTADO if e in siguientes]
        puede_asignarse = ticket.asignado_id != perfil.pk and asignables(ticket.condominio_id).filter(pk=perfil.pk).exists()
        otros = list(
            tickets_visibles(perfil).filter(usuario_id=ticket.usuario_id).exclude(pk=ticket.pk)
            .order_by("-actualizado")[:5]
        )

    return {
        "ticket": ticket,
        "adjuntos_ticket": ticket.pruebas.filter(comentario__isnull=True),
        "todos_adjuntos": ticket.pruebas.filter(Q(comentario__isnull=True) | Q(comentario__interno=False))
        if not gestiona else ticket.pruebas.all(),
        "linea": linea,
        "gestiona": gestiona,
        "comentario_form": comentario_form if puede_comentar(perfil, ticket) else None,
        "gestion_form": gestion_form,
        "botones_estado": botones_estado,
        "puede_asignarse": puede_asignarse,
        "otros_tickets": otros,
        "acciones": acciones_residente(perfil, ticket),
        "puede_editar": puede_editar(perfil, ticket),
    }


def _render_bandeja(request, ticket=None, status=200, **extra):
    perfil = request.perfil
    contexto = _bandeja(request, perfil)
    if ticket is not None:
        contexto.update(_contexto_ticket(perfil, ticket, **extra))
    return render(request, "tickets/bandeja.html", contexto, status=status)


@con_perfil
def lista(request):
    return _render_bandeja(request)


@con_perfil
def detalle(request, ticket_id):
    return _render_bandeja(request, _ticket_visible(request.perfil, ticket_id))


# ============================================================== CREAR / EDITAR
@con_perfil
def crear(request):
    perfil = request.perfil
    inicial = {"categoria": request.GET["categoria"]} if request.GET.get("categoria") in Ticket.Categoria.values else None
    form = TicketForm(request.POST or None, request.FILES or None, perfil=perfil, initial=inicial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        ticket = services.crear_ticket(
            perfil,
            condominio=d["condominio_final"],
            titulo=d["titulo"],
            descripcion=d["descripcion"],
            categoria=d["categoria"],
            unidad=d.get("unidad"),
            archivos=d["archivos"],
        )
        messages.success(request, f"Ticket #{ticket.pk} creado. Te avisaremos cuando haya novedades.")
        return redirect("tickets:detalle", ticket.pk)
    return render(request, "tickets/crear.html", {"form": form})


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


# ============================================================== ACCIONES
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
        return _render_bandeja(request, ticket, status=400, comentario_form=form)
    services.comentar(
        perfil, ticket,
        texto=form.cleaned_data["texto"],
        interno=form.cleaned_data.get("interno", False),
        archivos=form.cleaned_data["archivos"],
        es_staff=gestiona,
    )
    return _volver(request, ticket)


@con_perfil
@require_POST
def gestionar(request, ticket_id):
    perfil = request.perfil
    ticket = _ticket_visible(perfil, ticket_id)
    if not puede_gestionar(perfil, ticket):
        raise PermissionDenied
    form = GestionForm(request.POST, perfil=perfil, ticket=ticket)
    if not form.is_valid():
        return _render_bandeja(request, ticket, status=400, gestion_form=form)
    try:
        eventos = services.actualizar(perfil, ticket, **form.cleaned_data)
    except ValidationError as e:
        form.add_error(None, e)
        return _render_bandeja(request, ticket, status=400, gestion_form=form)
    if eventos:
        messages.success(request, "Ticket actualizado.")
    else:
        messages.info(request, "No hubo cambios.")
    return _volver(request, ticket)


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
    # "motivo" viene de los botones sueltos; "texto", si se usó el cuadro de mensaje
    motivo = (request.POST.get("motivo") or request.POST.get("texto") or "").strip()
    if motivo:
        services.comentar(perfil, Ticket.objects.get(pk=ticket.pk), texto=motivo)
    messages.success(request, TEXTO_ACCION[nombre])
    return _volver(request, ticket)


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
    # Imágenes y videos se muestran en el navegador; el resto se descarga
    descargar = not (adj.es_imagen or adj.es_video) or "descargar" in request.GET
    return FileResponse(f, as_attachment=descargar, filename=adj.nombre or None)

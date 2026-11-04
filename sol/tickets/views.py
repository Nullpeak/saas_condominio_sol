import mimetypes
import os
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Unidad, Usuario
from .models import Ticket, TicketArchivo
from .validators import EXTENSIONES_IMAGEN, EXTENSIONES_VIDEO, validar_adjunto


ROLES_TICKETS = ("Super Administrador", "Administrador", "Gestor", "Residente")
ROLES_GESTION = ("Super Administrador", "Administrador", "Gestor")


def perfil_actual(request):
    return request.user.usuario


def es_super(usuario):
    return usuario.rol == "Super Administrador"


def unidades_visibles(usuario):
    unidades = Unidad.objects.select_related("edificio", "edificio__condominio")
    if es_super(usuario):
        return unidades.order_by("edificio__condominio__nombre", "edificio__nombre", "numero_unidad")
    if usuario.rol == "Residente":
        return unidades.filter(Q(dueno=usuario) | Q(arrendatario=usuario)).distinct().order_by(
            "edificio__nombre", "numero_unidad"
        )
    condominios = usuario.condominios_asignados()
    if not condominios.exists():
        return unidades.none()
    return unidades.filter(edificio__condominio__in=condominios).order_by(
        "edificio__nombre", "numero_unidad"
    )


def tickets_visibles(usuario):
    tickets = Ticket.objects.select_related(
        "condominio", "unidad", "unidad__edificio", "usuario", "asignado", "resuelto_por"
    ).prefetch_related("archivos")
    if usuario.rol == "Residente":
        return tickets.filter(usuario=usuario)
    if not es_super(usuario):
        condominios = usuario.condominios_asignados()
        if not condominios.exists():
            return tickets.none()
        tickets = tickets.filter(condominio__in=condominios)
        if usuario.rol == "Gestor":
            tickets = tickets.filter(Q(asignado__isnull=True) | Q(asignado=usuario))
        return tickets
    return tickets


def filtrar_unidad(unidades, unidad_id):
    if not unidad_id:
        return None
    try:
        return unidades.filter(pk=int(unidad_id)).first()
    except (TypeError, ValueError):
        return None


def tickets_lista_url():
    return reverse("tickets:lista")


@login_required
def lista(request):
    usuario = perfil_actual(request)
    if usuario.rol not in ROLES_TICKETS:
        return redirect("inicio")
    if not es_super(usuario) and not usuario.condominios_asignados().exists():
        messages.error(request, "Tu usuario todavía no tiene un condominio asignado.")
        return redirect("inicio")

    tickets = tickets_visibles(usuario)
    unidades = unidades_visibles(usuario)
    vista = request.GET.get("vista", "activos").strip()
    if vista not in ("activos", "historial"):
        vista = "activos"
    if vista == "historial":
        tickets = tickets.filter(estado__in=(Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO))
    else:
        tickets = tickets.exclude(estado__in=(Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO))
    busqueda = request.GET.get("q", "").strip()
    if busqueda:
        tickets = tickets.filter(Q(titulo__icontains=busqueda) | Q(descripcion__icontains=busqueda))
    estado = request.GET.get("estado", "").strip()
    if estado in [value for value, _label in Ticket.Estado.choices]:
        tickets = tickets.filter(estado=estado)
    else:
        estado = ""
    unidad_id = request.GET.get("unidad", "").strip()
    unidad = filtrar_unidad(unidades, unidad_id)
    if unidad:
        tickets = tickets.filter(unidad=unidad)
    elif unidad_id:
        unidad_id = ""

    fecha_desde = request.GET.get("desde", "").strip()
    if fecha_desde:
        try:
            tickets = tickets.filter(creado__date__gte=date.fromisoformat(fecha_desde))
        except ValueError:
            fecha_desde = ""
    fecha_hasta = request.GET.get("hasta", "").strip()
    if fecha_hasta:
        try:
            tickets = tickets.filter(creado__date__lte=date.fromisoformat(fecha_hasta))
        except ValueError:
            fecha_hasta = ""

    tickets = list(tickets.order_by("-creado", "-id"))
    for ticket in tickets:
        for archivo in ticket.archivos.all():
            extension = os.path.splitext(archivo.archivo.name)[1].lower()
            archivo.es_imagen = extension in EXTENSIONES_IMAGEN
            archivo.es_video = extension in EXTENSIONES_VIDEO

    gestores = Usuario.objects.filter(rol="Gestor", activo=True)
    if not es_super(usuario):
        gestores = gestores.filter(condominio__in=usuario.condominios_asignados())

    return render(request, "tickets/lista.html", {
        "usuario_actual": usuario,
        "tickets": tickets,
        "unidades": unidades,
        "gestores": gestores.order_by("nombre", "apellido"),
        "estados": Ticket.Estado.choices,
        "prioridades": Ticket.Prioridad.choices,
        "busqueda": busqueda,
        "estado_filtro": estado,
        "unidad_filtro": unidad_id,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "puede_gestionar": usuario.rol in ROLES_GESTION,
        "puede_asignar": usuario.rol in ("Super Administrador", "Administrador"),
        "vista": vista,
    })


@login_required
def crear(request):
    usuario = perfil_actual(request)
    if usuario.rol not in ROLES_TICKETS:
        return redirect("inicio")
    if not es_super(usuario) and not usuario.condominios_asignados().exists():
        messages.error(request, "Tu usuario todavía no tiene un condominio asignado.")
        return redirect("inicio")

    unidades = unidades_visibles(usuario)
    errores = []
    if request.method == "POST":
        titulo = request.POST.get("titulo", "").strip()
        descripcion = request.POST.get("descripcion", "").strip()
        unidad_id = request.POST.get("unidad", "").strip()
        prioridad = request.POST.get("prioridad", "").strip() if usuario.rol in ("Super Administrador", "Administrador") else ""
        archivos = request.FILES.getlist("adjuntos")

        unidad = filtrar_unidad(unidades, unidad_id)
        if not unidad:
            errores.append("Selecciona una unidad válida.")
        if not titulo:
            errores.append("Escribe un nombre para el ticket.")
        elif len(titulo) > 150:
            errores.append("El nombre no puede superar 150 caracteres.")
        if not descripcion:
            errores.append("Describe el problema o solicitud.")
        if prioridad and prioridad not in [value for value, _label in Ticket.Prioridad.choices]:
            errores.append("La prioridad seleccionada no es válida.")
        if len(archivos) > 5:
            errores.append("Puedes adjuntar hasta 5 archivos por ticket.")
        for archivo in archivos:
            try:
                validar_adjunto(archivo)
            except ValidationError as error:
                errores.extend(error.messages)

        if not errores:
            ticket = Ticket(
                condominio=unidad.edificio.condominio,
                unidad=unidad,
                usuario=usuario,
                titulo=titulo,
                descripcion=descripcion,
                prioridad=prioridad,
            )
            ticket.full_clean()
            ticket.save()
            for archivo in archivos:
                TicketArchivo.objects.create(ticket=ticket, archivo=archivo)
            messages.success(request, "El ticket se creó correctamente.")
            return redirect(tickets_lista_url())

    return render(request, "tickets/crear.html", {
        "usuario_actual": usuario,
        "unidades": unidades,
        "prioridades": Ticket.Prioridad.choices,
        "puede_definir_prioridad": usuario.rol in ("Super Administrador", "Administrador"),
        "errores": errores,
        "datos": request.POST if request.method == "POST" else {},
        "es_residente": usuario.rol == "Residente",
        "unidad_unica": unidades.count() == 1,
    })


@login_required
def asignar(request, ticket_id):
    usuario = perfil_actual(request)
    if request.method != "POST" or usuario.rol not in ("Super Administrador", "Administrador"):
        return redirect("tickets:lista")
    ticket = get_object_or_404(tickets_visibles(usuario), pk=ticket_id)
    gestor_id = request.POST.get("gestor", "").strip()
    if not gestor_id:
        ticket.asignado = None
    else:
        try:
            gestor = Usuario.objects.get(pk=int(gestor_id), rol="Gestor", activo=True)
        except (Usuario.DoesNotExist, TypeError, ValueError):
            messages.error(request, "Selecciona un gestor válido.")
            return redirect("tickets:lista")
        if not usuario.puede_acceder_condominio(ticket.condominio_id):
            messages.error(request, "No tienes acceso al condominio de este ticket.")
            return redirect("tickets:lista")
        if gestor.condominio_id != ticket.condominio_id:
            messages.error(request, "El gestor debe pertenecer al condominio del ticket.")
            return redirect("tickets:lista")
        ticket.asignado = gestor
    ticket.save(update_fields=["asignado"])
    messages.success(request, "Se actualizó la asignación del ticket.")
    return redirect("tickets:lista")


@login_required
def cambiar_estado(request, ticket_id):
    usuario = perfil_actual(request)
    if request.method != "POST" or usuario.rol not in ROLES_GESTION:
        return redirect("tickets:lista")
    ticket = get_object_or_404(tickets_visibles(usuario), pk=ticket_id)
    nuevo_estado = request.POST.get("estado", "").strip()
    estados_validos = [value for value, _label in Ticket.Estado.choices]
    if nuevo_estado not in estados_validos:
        messages.error(request, "Selecciona un estado válido.")
        return redirect("tickets:lista")

    ticket.estado = nuevo_estado
    if nuevo_estado in (Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO):
        ticket.resuelto = timezone.now()
        ticket.resuelto_por = usuario
    ticket.save(update_fields=["estado", "resuelto", "resuelto_por"])
    messages.success(request, "Se actualizó el estado del ticket.")
    return redirect("tickets:lista")


@login_required
def archivo(request, archivo_id):
    usuario = perfil_actual(request)
    adjunto = get_object_or_404(
        TicketArchivo.objects.select_related("ticket", "ticket__usuario", "ticket__condominio"),
        pk=archivo_id,
    )
    ticket = adjunto.ticket
    permitido = (
        usuario.rol in ROLES_TICKETS
        and tickets_visibles(usuario).filter(pk=ticket.pk).exists()
    )
    if not permitido:
        messages.error(request, "No tienes acceso a este archivo.")
        return redirect("tickets:lista")

    content_type = mimetypes.guess_type(adjunto.archivo.name)[0] or "application/octet-stream"
    return FileResponse(
        adjunto.archivo.open("rb"),
        content_type=content_type,
        as_attachment=False,
        filename=os.path.basename(adjunto.archivo.name),
    )

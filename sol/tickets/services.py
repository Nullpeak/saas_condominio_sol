"""
Todas las escrituras de tickets pasan por aquí: así el historial (TicketEvento) y la fecha
de último movimiento quedan siempre al día, venga el cambio de la vista que venga.

`ticket._actor` le dice a las señales de notis quién hizo el cambio (para no notificarse a sí mismo).
"""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Reservacion
from .models import Ticket, TicketArchivo, TicketComentario, TicketEvento, TicketVisita

E = Ticket.Estado


def _guardar_archivos(ticket, archivos, comentario=None):
    for f in archivos or []:
        TicketArchivo.objects.create(
            ticket=ticket, comentario=comentario, archivo=f, nombre=f.name[:150]
        )


def _aplicar_estado(ticket, nuevo):
    """Ajusta las fechas de resuelto/cerrado según el estado al que se pasa."""
    ahora = timezone.now()
    ticket.estado = nuevo
    if nuevo == E.RESUELTO:
        ticket.resuelto = ahora
        ticket.cerrado = None
    elif nuevo == E.CERRADO:
        ticket.cerrado = ahora  # "resuelto" se conserva si venía de resuelto
    else:  # reabierto o en curso
        ticket.resuelto = None
        ticket.cerrado = None


@transaction.atomic
def crear_ticket(perfil, *, condominio, titulo, descripcion, categoria, unidad=None, archivos=None, visitas=None):
    ticket = Ticket(
        condominio=condominio,
        usuario=perfil,
        unidad=unidad,
        categoria=categoria,
        titulo=titulo,
        descripcion=descripcion,
    )
    ticket._actor = perfil
    ticket.save()
    TicketEvento.objects.create(ticket=ticket, autor=perfil, tipo=TicketEvento.Tipo.CREADO)
    _guardar_archivos(ticket, archivos)
    if categoria == Ticket.Categoria.VISITAS:
        TicketVisita.objects.bulk_create([TicketVisita(ticket=ticket, **v) for v in visitas or []])
    return ticket


@transaction.atomic
def comentar(perfil, ticket, *, texto, interno=False, archivos=None, es_staff=False):
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    comentario = TicketComentario.objects.create(
        ticket=ticket, autor=perfil, texto=texto, interno=interno and es_staff
    )
    _guardar_archivos(ticket, archivos, comentario)

    campos = ["actualizado"]
    ticket.actualizado = timezone.now()
    # Si el staff estaba esperando al residente y éste responde, vuelve a revisión solo
    if not es_staff and ticket.estado == E.ESPERANDO:
        TicketEvento.objects.create(
            ticket=ticket, autor=perfil, tipo=TicketEvento.Tipo.ESTADO,
            anterior=ticket.get_estado_display(), nuevo=E.EN_REVISION.label,
        )
        _aplicar_estado(ticket, E.EN_REVISION)
        campos += ["estado", "resuelto", "cerrado"]
    ticket._actor = perfil
    ticket.save(update_fields=campos)
    return comentario


@transaction.atomic
def actualizar(perfil, ticket, **cambios):
    """
    cambios admite: estado, prioridad, categoria, asignado (Usuario o None).
    Solo registra lo que realmente cambió. Devuelve la lista de eventos creados.
    """
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    eventos = []

    def registrar(tipo, anterior, nuevo):
        eventos.append(TicketEvento(ticket=ticket, autor=perfil, tipo=tipo,
                                    anterior=anterior or "", nuevo=nuevo or ""))

    if "estado" in cambios and cambios["estado"] != ticket.estado:
        registrar(TicketEvento.Tipo.ESTADO, ticket.get_estado_display(), E(cambios["estado"]).label)
        _aplicar_estado(ticket, cambios["estado"])

    if "prioridad" in cambios and (cambios["prioridad"] or "") != ticket.prioridad:
        nueva = cambios["prioridad"] or ""
        registrar(TicketEvento.Tipo.PRIORIDAD, ticket.get_prioridad_display(),
                  Ticket.Prioridad(nueva).label if nueva else "Sin prioridad")
        ticket.prioridad = nueva

    if "categoria" in cambios and cambios["categoria"] and cambios["categoria"] != ticket.categoria:
        registrar(TicketEvento.Tipo.CATEGORIA, ticket.get_categoria_display(),
                  Ticket.Categoria(cambios["categoria"]).label)
        ticket.categoria = cambios["categoria"]

    if "asignado" in cambios:
        nuevo = cambios["asignado"]
        if (nuevo.pk if nuevo else None) != ticket.asignado_id:
            registrar(TicketEvento.Tipo.ASIGNADO, str(ticket.asignado or ""), str(nuevo or ""))
            ticket.asignado = nuevo

    if eventos:
        ticket.actualizado = timezone.now()
        ticket.full_clean(exclude=["usuario", "condominio"])
        ticket._actor = perfil
        ticket.save()
        TicketEvento.objects.bulk_create(eventos)
    return eventos


# ============================================================== VISITAS
def _cerrar_revision_visitas(perfil, ticket):
    """Cuando ya no quedan visitas pendientes, el ticket pasa solo a Resuelto."""
    if ticket.visitas.filter(estado=TicketVisita.Estado.PENDIENTE).exists():
        ticket.actualizado = timezone.now()
        ticket._actor = perfil
        ticket.save(update_fields=["actualizado"])
    elif ticket.esta_abierto:
        actualizar(perfil, ticket, estado=E.RESUELTO)


def _bloquear_pendiente(perfil, visita):
    visita = TicketVisita.objects.select_for_update().select_related("ticket__unidad").get(pk=visita.pk)
    if visita.estado != TicketVisita.Estado.PENDIENTE:
        raise ValidationError("Esa visita ya fue revisada.")
    if visita.ticket.estado == E.CERRADO:
        raise ValidationError("El ticket está cerrado.")
    return visita


@transaction.atomic
def aprobar_visita(perfil, visita):
    """
    Registra la visita: crea una Reservacion de invitado ya aprobada. Reservacion.save()
    crea el Invitado de la unidad; aquí solo se le completa la patente.
    """
    visita = _bloquear_pendiente(perfil, visita)
    ticket = visita.ticket
    if not ticket.unidad_id:
        raise ValidationError("El ticket no tiene unidad: no se puede registrar la visita.")

    reserva = Reservacion(
        tipo_reserva=Reservacion.Tipo.INVITADO,
        usuario=ticket.usuario,
        unidad=ticket.unidad,
        fecha_reserva=visita.llegada,
        fin_reserva=visita.salida,
        estado=Reservacion.Estado.APROBADO,
        nombre_invitado=visita.nombre,
        notas=f"Aprobada desde el ticket #{ticket.pk}" + (f" · RUT {visita.rut}" if visita.rut else ""),
    )
    reserva.full_clean()
    reserva._desde_ticket = True  # notis: no avisar "nueva reservación" al staff, ya la aprobó
    reserva.save()
    if visita.patente and reserva.invitado_id:
        reserva.invitado.patente_vehiculo = visita.patente
        reserva.invitado.save(update_fields=["patente_vehiculo"])

    visita.estado = TicketVisita.Estado.APROBADA
    visita.reservacion = reserva
    visita.revisada_por = perfil
    visita.revisada = timezone.now()
    visita.save()
    TicketEvento.objects.create(ticket=ticket, autor=perfil, tipo=TicketEvento.Tipo.VISITA,
                                anterior=visita.nombre, nuevo="aprobó")
    _cerrar_revision_visitas(perfil, ticket)
    return visita


@transaction.atomic
def rechazar_visita(perfil, visita, motivo=""):
    visita = _bloquear_pendiente(perfil, visita)
    visita.estado = TicketVisita.Estado.RECHAZADA
    visita.motivo_rechazo = motivo[:255]
    visita.revisada_por = perfil
    visita.revisada = timezone.now()
    visita.save()
    TicketEvento.objects.create(ticket=visita.ticket, autor=perfil, tipo=TicketEvento.Tipo.VISITA,
                                anterior=visita.nombre, nuevo="rechazó")
    _cerrar_revision_visitas(perfil, visita.ticket)
    return visita

"""
Todas las escrituras de tickets pasan por aquí: así el historial (TicketEvento) y la fecha
de último movimiento quedan siempre al día, venga el cambio de la vista que venga.

`ticket._actor` le dice a las señales de notis quién hizo el cambio (para no notificarse a sí mismo).
"""
from django.db import transaction
from django.utils import timezone

from .models import Ticket, TicketArchivo, TicketComentario, TicketEvento

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
def crear_ticket(perfil, *, condominio, titulo, descripcion, categoria, unidad=None, archivos=None):
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
def editar(perfil, ticket, *, titulo, descripcion, categoria):
    """El residente corrige su ticket mientras nadie lo ha tomado. Devuelve True si cambió algo."""
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if (ticket.titulo, ticket.descripcion, ticket.categoria) == (titulo, descripcion, categoria):
        return False
    anterior = ticket.get_categoria_display()
    ticket.titulo, ticket.descripcion = titulo, descripcion
    eventos = [TicketEvento(ticket=ticket, autor=perfil, tipo=TicketEvento.Tipo.EDICION)]
    if categoria != ticket.categoria:
        ticket.categoria = categoria
        eventos.append(TicketEvento(ticket=ticket, autor=perfil, tipo=TicketEvento.Tipo.CATEGORIA,
                                    anterior=anterior, nuevo=ticket.get_categoria_display()))
    ticket.actualizado = timezone.now()
    ticket._actor = perfil
    ticket.save(update_fields=["titulo", "descripcion", "categoria", "actualizado"])
    TicketEvento.objects.bulk_create(eventos)
    return True


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

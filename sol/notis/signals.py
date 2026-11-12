import functools
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.urls import reverse

from anuncios.models import AnuncioResidente
from core.models import Invitado, Reservacion, Usuario
from gastos_comunes.models import PagoOnline, Periodo
from invitaciones.models import Invitacion
from tickets.models import Ticket, TicketComentario, TicketVisita
from visitas.models import Visita

from .models import Notification

ROLES_STAFF = (Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR)
ROLES_ADMIN = (Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR)


logger = logging.getLogger(__name__)


def seguro(func):
    """
    Una notificación nunca debe romper la operación principal (registro, pago, etc.).
    Si algo falla se registra en el log y el flujo sigue. El savepoint evita dejar
    la transacción en mal estado si el error fue de base de datos.
    """
    @functools.wraps(func)
    def envoltura(*args, **kwargs):
        try:
            with transaction.atomic():
                return func(*args, **kwargs)
        except Exception:
            logger.exception("Error generando notificación en %s", func.__name__)
    return envoltura


# ---------------------------------------------------------------- helpers
def notificar(destinatarios, mensaje, remitente=None, url=""):
    """
    Crea una notificación por cada Usuario con cuenta (user != None).
    - Deduplica destinatarios.
    - No notifica al propio remitente.
    - Se crea recién cuando la transacción hace commit.
    """
    remitente_user = remitente.user if remitente else None
    vistos, nuevas = set(), []
    for u in destinatarios:
        if u is None or u.user_id is None or u.user_id in vistos:
            continue
        vistos.add(u.user_id)
        if remitente_user and u.user_id == remitente_user.pk:
            continue
        nuevas.append(
            Notification(recipient_id=u.user_id, sender=remitente_user, message=mensaje, url=url)
        )
    if nuevas:
        transaction.on_commit(lambda: Notification.objects.bulk_create(nuevas))


def staff_del_condominio(condominio_id, roles=ROLES_STAFF):
    """
    Staff del condominio. El Super Administrador se incluye siempre, y también los
    administradores que lo tienen entre sus condominios administrados.
    """
    filtro = Q(condominio_id=condominio_id, rol__in=roles) | Q(rol=Usuario.Rol.SUPER)
    if Usuario.Rol.ADMINISTRADOR in roles:
        filtro |= Q(condominios_administrados=condominio_id, rol=Usuario.Rol.ADMINISTRADOR)
    return Usuario.objects.filter(filtro, activo=True, user__isnull=False).distinct()


def residentes_de_unidad(unidad):
    return [unidad.dueno, unidad.arrendatario]


def residentes_del_condominio(condominio_id):
    return Usuario.objects.filter(
        Q(condominio_id=condominio_id)
        | Q(unidades_propias__edificio__condominio_id=condominio_id)
        | Q(unidades_arrendadas__edificio__condominio_id=condominio_id),
        activo=True,
        user__isnull=False,
    ).distinct()


# ------------------------------------------- guardar el estado anterior
@receiver(pre_save, sender=Usuario)
@receiver(pre_save, sender=Reservacion)
@receiver(pre_save, sender=Ticket)
@receiver(pre_save, sender=Periodo)
@receiver(pre_save, sender=PagoOnline)
@receiver(pre_save, sender=AnuncioResidente)
@receiver(pre_save, sender=Invitado)
@receiver(pre_save, sender=TicketVisita)
@receiver(pre_save, sender=Visita)
@seguro
def guardar_anterior(sender, instance, **kwargs):
    instance._anterior = (
        sender.objects.filter(pk=instance.pk).first() if instance.pk else None
    )


# ---------------------------------------------------------------- Usuario
@receiver(post_save, sender=Usuario)
@seguro
def notificar_cambio_perfil(sender, instance, created, **kwargs):
    nombre = str(instance)
    destinatarios = staff_del_condominio(instance.condominio_id, ROLES_ADMIN)

    # Usuario recién registrado
    if created:
        notificar(
            destinatarios,
            f"Nuevo usuario registrado: {nombre} ({instance.get_rol_display()}).",
            remitente=instance,
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if anterior is None:
        return

    if anterior.email != instance.email:
        notificar(destinatarios, f"{nombre} ha actualizado su correo electrónico.", remitente=instance)

    if anterior.avatar.name != instance.avatar.name:
        notificar(destinatarios, f"{nombre} ha actualizado su imagen de perfil.", remitente=instance)


# ------------------------------------------------------------ Reservacion
@receiver(post_save, sender=Reservacion)
@seguro
def notificar_reservacion(sender, instance, created, **kwargs):
    if created and getattr(instance, "_desde_ticket", False):
        return  # la aprobó el staff desde un ticket de visitas: el aviso lo da TicketVisita
    if created:
        notificar(
            staff_del_condominio(instance.unidad.edificio.condominio_id),
            f"Nueva reservación #{instance.pk} ({instance.get_tipo_reserva_display()}) "
            f"de {instance.usuario} para la unidad {instance.unidad.numero_unidad}.",
            remitente=instance.usuario,
            url=reverse("reservaciones_lista"),
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if not anterior or anterior.estado == instance.estado:
        return
    actor = getattr(instance, "_actor", None)
    url = reverse("reservaciones_lista")
    motivo = (instance.motivo_cancelacion or "").strip()
    detalle = f" Motivo: {motivo}" if instance.estado == Reservacion.Estado.CANCELADO and motivo else ""
    notificar(
        [instance.usuario],
        f"Tu reservación #{instance.pk} cambió a estado: {instance.get_estado_display()}.{detalle}",
        remitente=actor, url=url,
    )
    if instance.estado == Reservacion.Estado.CANCELADO and actor and actor.pk == instance.usuario_id:
        cuando = timezone.localtime(instance.fecha_reserva) if instance.fecha_reserva else None
        notificar(
            staff_del_condominio(instance.unidad.edificio.condominio_id),
            f"{actor} canceló la reservación #{instance.pk}"
            + (f" del {cuando:%d-%m-%Y %H:%M}." if cuando else ".")
            + (f" Motivo: {motivo}" if motivo else ""),
            remitente=actor, url=url,
        )


# ----------------------------------------------------------------- Ticket
# tickets/services.py deja en `instance._actor` quién hizo el cambio: así nadie se notifica a sí mismo.
def _url_ticket(ticket):
    from django.urls import reverse
    return reverse("tickets:detalle", args=[ticket.pk])


def _equipo_del_ticket(ticket):
    """El responsable si hay uno; si no, todo el staff del condominio."""
    return [ticket.asignado] if ticket.asignado_id else staff_del_condominio(ticket.condominio_id)


@receiver(post_save, sender=Ticket)
@seguro
def notificar_ticket(sender, instance, created, **kwargs):
    actor = getattr(instance, "_actor", None)
    url = _url_ticket(instance)

    if created:
        notificar(
            staff_del_condominio(instance.condominio_id),
            f"Nuevo ticket #{instance.pk}: {instance.titulo} (por {instance.usuario}).",
            remitente=instance.usuario,
            url=url,
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if not anterior:
        return

    if anterior.estado != instance.estado:
        if actor is not None and actor.pk == instance.usuario_id:
            # El residente confirmó, reabrió o canceló: se avisa al equipo
            notificar(
                _equipo_del_ticket(instance),
                f"{actor} dejó el ticket #{instance.pk} en estado: {instance.get_estado_display()}.",
                remitente=actor,
                url=url,
            )
        else:
            notificar(
                [instance.usuario],
                f"Tu ticket #{instance.pk} cambió a estado: {instance.get_estado_display()}.",
                remitente=actor,
                url=url,
            )

    if instance.asignado_id and anterior.asignado_id != instance.asignado_id:
        notificar(
            [instance.asignado],
            f"Se te asignó el ticket #{instance.pk}: {instance.titulo}.",
            remitente=actor,
            url=url,
        )


@receiver(post_save, sender=TicketComentario)
@seguro
def notificar_comentario_ticket(sender, instance, created, **kwargs):
    if not created:
        return
    ticket = instance.ticket
    autor = instance.autor
    url = _url_ticket(ticket) + "#responder"
    if autor.pk == ticket.usuario_id:
        destinatarios = _equipo_del_ticket(ticket)
        mensaje = f"{autor} respondió en el ticket #{ticket.pk}: {ticket.titulo}."
    elif instance.interno:
        # Nota interna: solo al responsable (nunca al residente)
        destinatarios = [ticket.asignado]
        mensaje = f"Nota interna de {autor} en el ticket #{ticket.pk}."
    else:
        destinatarios = [ticket.usuario, ticket.asignado]
        mensaje = f"Nueva respuesta de {autor} en el ticket #{ticket.pk}: {ticket.titulo}."
    notificar(destinatarios, mensaje, remitente=autor, url=url)


@receiver(post_save, sender=TicketVisita)
@seguro
def notificar_visita_ticket(sender, instance, created, **kwargs):
    anterior = getattr(instance, "_anterior", None)
    if created or not anterior or anterior.estado == instance.estado:
        return
    ticket = instance.ticket
    if instance.estado == TicketVisita.Estado.APROBADA:
        mensaje = (f"Tu visita {instance.nombre} fue aprobada y quedó registrada para el "
                   f"{instance.llegada:%d-%m-%Y %H:%M}.")
    elif instance.estado == TicketVisita.Estado.RECHAZADA:
        mensaje = f"Tu visita {instance.nombre} fue rechazada."
        if instance.motivo_rechazo:
            mensaje += f" Motivo: {instance.motivo_rechazo}"
    else:
        return
    notificar([ticket.usuario], mensaje, remitente=instance.revisada_por, url=_url_ticket(ticket))


# --------------------------------------------------------- Gastos comunes
@receiver(post_save, sender=Periodo)
@seguro
def notificar_periodo_publicado(sender, instance, created, **kwargs):
    """Al publicar un período, cada residente con liquidación recibe el aviso de su cobro."""
    anterior = getattr(instance, "_anterior", None)
    if not anterior or anterior.estado == instance.estado or instance.estado != Periodo.Estado.PUBLICADO:
        return
    periodo = f"{instance.mes:02d}/{instance.anio}"
    vence = f" Vence el {instance.vencimiento:%d-%m-%Y}." if instance.vencimiento else ""
    url = reverse("gastos_comunes:detalle", args=[instance.pk])
    for liq in instance.liquidaciones.select_related("unidad__dueno", "unidad__arrendatario"):
        notificar(
            residentes_de_unidad(liq.unidad),
            f"Ya está disponible tu gasto común de {periodo} (unidad {liq.unidad.numero_unidad}).{vence}",
            url=url,
        )


@receiver(post_save, sender=PagoOnline)
@seguro
def notificar_pago_online(sender, instance, created, **kwargs):
    """Resultado del pago online: aprobado o rechazado (pendiente no avisa para no hacer ruido)."""
    anterior = getattr(instance, "_anterior", None)
    if not anterior or anterior.estado == instance.estado:
        return
    E = PagoOnline.Estado
    monto = f"${instance.monto:,.0f}".replace(",", ".")
    periodo = instance.liquidacion.periodo
    texto = {
        E.APROBADO: f"Tu pago de {monto} del gasto común {periodo.mes:02d}/{periodo.anio} fue aprobado.",
        E.RECHAZADO: f"Tu pago de {monto} del gasto común {periodo.mes:02d}/{periodo.anio} fue rechazado. "
                     "El saldo sigue pendiente.",
    }.get(instance.estado)
    if texto:
        notificar([instance.usuario], texto,
                  url=reverse("gastos_comunes:pago_resultado", args=[instance.referencia_externa]))


# ------------------------------------------------------ Anuncios residentes
@receiver(post_save, sender=AnuncioResidente)
@seguro
def notificar_anuncio_residente(sender, instance, created, **kwargs):
    E = AnuncioResidente.Estado
    if created and instance.estado == E.PENDIENTE:
        notificar(
            staff_del_condominio(instance.condominio_id),
            f"{instance.autor} publicó «{instance.titulo}» y espera tu revisión.",
            remitente=instance.autor,
            url=reverse("anuncios:lista") + "?tipo=residentes&estado=pendiente",
        )
        return
    anterior = getattr(instance, "_anterior", None)
    if not anterior or anterior.estado != E.PENDIENTE or anterior.estado == instance.estado:
        return
    if instance.estado == E.PUBLICADO:
        texto = f"Tu anuncio «{instance.titulo}» fue aprobado y ya está publicado."
    elif instance.estado == E.RECHAZADO:
        texto = f"Tu anuncio «{instance.titulo}» no fue aprobado."
    else:
        return
    notificar([instance.autor], texto, remitente=instance.moderado_por,
              url=reverse("anuncios:lista") + "?tipo=residentes")


# --------------------------------------------------------------- Invitado
@receiver(post_save, sender=Invitado)
@seguro
def notificar_invitado(sender, instance, created, **kwargs):
    anterior = getattr(instance, "_anterior", None)
    if created or not anterior or not instance.unidad_id:
        return
    if anterior.estado != instance.estado and instance.estado == Invitado.Estado.CHECK_IN:
        notificar(
            residentes_de_unidad(instance.unidad),
            f"Tu invitado {instance.nombre} acaba de llegar.",
        )


# ----------------------------------------------------------------- Visita
@receiver(post_save, sender=Visita)
@seguro
def notificar_visita(sender, instance, created, **kwargs):
    """Conserjería marcó la visita como presente: se avisa a los residentes de la unidad."""
    anterior = getattr(instance, "_anterior", None)
    if created or not anterior or not instance.unidad_id:
        return
    if anterior.estado != instance.estado and instance.estado == Visita.Estado.PRESENTE:
        notificar(
            residentes_de_unidad(instance.unidad),
            f"Tu visita {instance.nombre} acaba de llegar.",
        )


# ------------------------------------------------------------- Invitacion
def avisar_invitacion_usada(inv):
    """
    Avisa UNA sola vez a quien envió la invitación.
    El UPDATE condicional hace de candado: si dos disparadores llegan a la vez
    (señal + comando, por ejemplo), solo uno logra marcarla y envía el aviso.
    """
    reclamada = Invitacion.objects.filter(pk=inv.pk, uso_notificado=False).update(
        uso_notificado=True
    )
    if not reclamada:
        return
    notificar(
        [inv.invitado_por],
        f"La invitación enviada a {inv.email} ({inv.get_rol_display()}) fue aceptada.",
    )


# 1) Cuando `usada` se guarda con save()
@receiver(post_save, sender=Invitacion)
@seguro
def notificar_invitacion_usada(sender, instance, created, **kwargs):
    if not created and instance.usada is not None:
        avisar_invitacion_usada(instance)


# 2) Cuando se crea el Usuario: sirve aunque la vista escriba `usada` con update()
#    o lo haga antes/después de crear el usuario.
@receiver(post_save, sender=Usuario)
@seguro
def notificar_invitacion_por_registro(sender, instance, created, **kwargs):
    if not created or not instance.email:
        return
    inv = (
        Invitacion.objects.filter(
            Q(usada__isnull=False) | Q(expira__gt=timezone.now()),
            email__iexact=instance.email,
            condominio_id=instance.condominio_id,
            revocada=False,
            uso_notificado=False,
        )
        .select_related("invitado_por")
        .order_by("-creada")
        .first()
    )
    if inv:
        avisar_invitacion_usada(inv)

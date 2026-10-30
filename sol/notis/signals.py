import functools
import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

# Ajusta "core" al nombre real de la app donde está tu models.py
from core.models import Anuncio, Invitado, Pago, Reservacion, Ticket, Usuario
# Ajusta "invitaciones" al nombre real de esa app
from invitaciones.models import Invitacion

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
    """Staff del condominio. El Super Administrador se incluye siempre."""
    return Usuario.objects.filter(
        Q(condominio_id=condominio_id, rol__in=roles) | Q(rol=Usuario.Rol.SUPER),
        activo=True,
        user__isnull=False,
    )


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
@receiver(pre_save, sender=Pago)
@receiver(pre_save, sender=Invitado)
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
    if created:
        notificar(
            staff_del_condominio(instance.unidad.edificio.condominio_id),
            f"Nueva reservación #{instance.pk} ({instance.get_tipo_reserva_display()}) "
            f"de {instance.usuario} para la unidad {instance.unidad.numero_unidad}.",
            remitente=instance.usuario,
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if anterior and anterior.estado != instance.estado:
        notificar(
            [instance.usuario],
            f"Tu reservación #{instance.pk} cambió a estado: {instance.get_estado_display()}.",
        )


# ----------------------------------------------------------------- Ticket
@receiver(post_save, sender=Ticket)
@seguro
def notificar_ticket(sender, instance, created, **kwargs):
    if created:
        notificar(
            staff_del_condominio(instance.condominio_id),
            f"Nuevo ticket #{instance.pk}: {instance.titulo} (por {instance.usuario}).",
            remitente=instance.usuario,
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if not anterior:
        return

    if anterior.estado != instance.estado:
        notificar(
            [instance.usuario],
            f"Tu ticket #{instance.pk} cambió a estado: {instance.get_estado_display()}.",
        )

    if instance.asignado_id and anterior.asignado_id != instance.asignado_id:
        notificar(
            [instance.asignado],
            f"Se te asignó el ticket #{instance.pk}: {instance.titulo}.",
            remitente=instance.usuario,
        )


# ------------------------------------------------------------------- Pago
@receiver(post_save, sender=Pago)
@seguro
def notificar_pago(sender, instance, created, **kwargs):
    residentes = residentes_de_unidad(instance.unidad)

    if created:
        notificar(
            residentes,
            f"Nuevo cobro: {instance.costo.nombre} por ${instance.monto:,} "
            f"(vence el {instance.fecha_pago:%d-%m-%Y}).".replace(",", "."),
        )
        return

    anterior = getattr(instance, "_anterior", None)
    if anterior and anterior.estado != instance.estado:
        notificar(
            residentes,
            f"Tu pago de {instance.costo.nombre} ahora está: {instance.get_estado_display()}.",
        )


# ---------------------------------------------------------------- Anuncio
@receiver(post_save, sender=Anuncio)
@seguro
def notificar_anuncio(sender, instance, created, **kwargs):
    if created:
        notificar(
            residentes_del_condominio(instance.condominio_id),
            f"Nuevo anuncio: {instance.titulo}",
            remitente=instance.usuario,
        )


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
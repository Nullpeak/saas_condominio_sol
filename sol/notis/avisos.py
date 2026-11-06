"""
Avisos que no nacen de guardar un modelo, sino del paso del tiempo.
Los ejecuta Celery beat (notis/tasks.py) o, a mano, sus comandos de manage.py.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from invitaciones.models import Invitacion
from mantenimientos.models import RegistroMantenimiento

from .signals import avisar_invitacion_usada, notificar, staff_del_condominio


def avisar_invitaciones():
    """Avisa a quien invitó cuando la invitación se usó (respaldo de la señal) o expiró sin usarse."""
    usadas = 0
    for inv in Invitacion.objects.filter(usada__isnull=False, uso_notificado=False).select_related("invitado_por__user"):
        avisar_invitacion_usada(inv)
        usadas += 1

    expiradas = 0
    pendientes = Invitacion.objects.filter(
        usada__isnull=True, revocada=False, expira__lte=timezone.now(), expiracion_notificada=False,
    ).select_related("invitado_por__user")
    for inv in pendientes:
        with transaction.atomic():
            notificar(
                [inv.invitado_por],
                f"La invitación enviada a {inv.email} ({inv.get_rol_display()}) expiró sin ser utilizada.",
            )
            inv.expiracion_notificada = True
            inv.save(update_fields=["expiracion_notificada"])
        expiradas += 1
    return {"usadas": usadas, "expiradas": expiradas}


def avisar_mantenciones_proximas(dias=3):
    """Avisa al staff de las mantenciones cuya próxima fecha cae entre hoy y `dias` días más."""
    hoy = timezone.localdate()
    hay_uno_mas_nuevo = RegistroMantenimiento.objects.filter(
        elemento=OuterRef("elemento"), fecha__gt=OuterRef("fecha")
    )
    registros = (
        RegistroMantenimiento.objects
        .filter(proximo_mantenimiento__range=(hoy, hoy + timedelta(days=dias)))
        .exclude(Exists(hay_uno_mas_nuevo))  # solo cuenta el último registro de cada equipo
        .select_related("elemento")
    )
    total = 0
    for r in registros:
        if r.proximo_notificado == r.proximo_mantenimiento:
            continue
        faltan = (r.proximo_mantenimiento - hoy).days
        cuando = "hoy" if faltan == 0 else ("mañana" if faltan == 1 else f"en {faltan} días")
        with transaction.atomic():
            notificar(
                staff_del_condominio(r.elemento.condominio_id),
                f"Mantención de {r.elemento.nombre} programada para {cuando} "
                f"({r.proximo_mantenimiento:%d-%m-%Y}).",
                url=reverse("mantenimientos:detalle", args=[r.elemento_id]),
            )
            RegistroMantenimiento.objects.filter(pk=r.pk).update(proximo_notificado=r.proximo_mantenimiento)
        total += 1
    return total

from contextvars import ContextVar

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import (
    AreaComun, Condominio, Edificio, EspacioEstacionamiento,
    HistorialAdministrativo, HorarioAreaComun, Pago, Reservacion, Unidad, Usuario,
)


_actor_actual = ContextVar("actor_auditoria", default=None)
_MODELOS = {}
_CAMPOS_EXCLUIDOS = {
    "password", "access_token", "refresh_token", "token", "secret",
    "client_secret", "clave", "contrasena", "webhook_secret",
}


def asignar_actor(actor):
    return _actor_actual.set(actor)


def limpiar_actor(token):
    _actor_actual.reset(token)


def _nombres_campos(modelo):
    return [
        campo.attname for campo in modelo._meta.concrete_fields
        if not campo.primary_key
        and campo.name not in {"creado", "actualizado", "fecha", "fecha_creacion"}
        and campo.name.lower() not in _CAMPOS_EXCLUIDOS
    ]


def _condominio_id(instancia):
    rutas = (
        ("condominio_id",),
        ("edificio", "condominio_id"),
        ("unidad", "edificio", "condominio_id"),
        ("periodo", "condominio_id"),
        ("liquidacion", "periodo", "condominio_id"),
        ("elemento", "condominio_id"),
        ("registro", "elemento", "condominio_id"),
        ("ticket", "condominio_id"),
        ("area_comun", "condominio_id"),
    )
    if isinstance(instancia, Condominio):
        return instancia.pk
    for ruta in rutas:
        objeto = instancia
        try:
            for atributo in ruta:
                objeto = getattr(objeto, atributo)
            if objeto:
                return objeto
        except (AttributeError, ValueError):
            continue
    return None


def _registrar(instancia, accion, campos):
    actor = _actor_actual.get()
    if actor is None or not actor.pk:
        return
    modelo = instancia._meta
    HistorialAdministrativo.objects.create(
        actor=actor,
        condominio_id=_condominio_id(instancia),
        accion=accion,
        modelo=f"{modelo.app_label}.{modelo.verbose_name}",
        objeto_id=str(instancia.pk),
        objeto=f"{modelo.verbose_name} #{instancia.pk}",
        campos=sorted(set(campos)),
    )


_MODELOS_TRACKING = (
    Condominio, Edificio, Unidad, Usuario, AreaComun, HorarioAreaComun,
    Reservacion, Pago, EspacioEstacionamiento,
)


def registrar_modelos_adicionales():
    """Called by CoreConfig.ready after every Django app has loaded its models."""
    from anuncios.models import AnuncioOficial, AnuncioResidente
    from gastos_comunes.models import ItemGasto, Liquidacion, PagoRegistrado, Periodo
    from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
    from tickets.models import Ticket
    from visitas.models import Visita

    return _MODELOS_TRACKING + (
        AnuncioOficial, AnuncioResidente, ItemGasto, Liquidacion, PagoRegistrado,
        Periodo, ElementoMantenimiento, RegistroMantenimiento, Ticket,
        Visita,
    )


def conectar_senales():
    for modelo in registrar_modelos_adicionales():
        if modelo in _MODELOS:
            continue
        _MODELOS[modelo] = True
        pre_save.connect(_antes_de_guardar, sender=modelo, weak=False, dispatch_uid=f"audit-pre-{modelo._meta.label_lower}")
        post_save.connect(_despues_de_guardar, sender=modelo, weak=False, dispatch_uid=f"audit-post-{modelo._meta.label_lower}")
        post_delete.connect(_despues_de_eliminar, sender=modelo, weak=False, dispatch_uid=f"audit-delete-{modelo._meta.label_lower}")


def _antes_de_guardar(sender, instance, raw=False, **kwargs):
    if raw or _actor_actual.get() is None:
        instance._audit_campos = []
        return
    campos = _nombres_campos(sender)
    if instance._state.adding:
        instance._audit_campos = campos
        return
    valores = sender.objects.filter(pk=instance.pk).values(*campos).first()
    instance._audit_campos = [
        campo for campo in campos
        if valores is not None and valores.get(campo) != getattr(instance, campo)
    ]


def _despues_de_guardar(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    campos = getattr(instance, "_audit_campos", [])
    if created or campos:
        _registrar(
            instance,
            HistorialAdministrativo.Accion.CREAR if created else HistorialAdministrativo.Accion.ACTUALIZAR,
            campos,
        )


def _despues_de_eliminar(sender, instance, **kwargs):
    _registrar(instance, HistorialAdministrativo.Accion.ELIMINAR, ())

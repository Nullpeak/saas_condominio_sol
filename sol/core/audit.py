from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import (
    AreaComun, Condominio, Edificio, EspacioEstacionamiento,
    HistorialAdministrativo, HorarioAreaComun, Pago, Reservacion, SolicitudBajaArrendatario, Unidad, Usuario,
)


_actor_actual = ContextVar("actor_auditoria", default=None)
_omitir_auditoria_liquidaciones = ContextVar("omitir_auditoria_liquidaciones", default=False)
_MODELOS = {}
_CAMPOS_EXCLUIDOS = {
    "password", "access_token", "refresh_token", "token", "secret",
    "client_secret", "clave", "contrasena", "webhook_secret",
}


def _normalizar_valor(campo, valor, instancia=None):
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, bool):
        return "Sí" if valor else "No"
    if campo.is_relation:
        try:
            if instancia is not None and getattr(instancia, campo.attname, None) == valor:
                return str(getattr(instancia, campo.name))
            relacionado = campo.related_model.objects.filter(pk=valor).first()
            return str(relacionado) if relacionado else "Sin asignar"
        except Exception:
            return str(valor)
    if campo.choices:
        return str(dict(campo.choices).get(valor, valor))
    if isinstance(valor, datetime):
        if timezone.is_aware(valor):
            valor = timezone.localtime(valor)
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _valor_legible(campo, instancia):
    """Return a JSON-safe, human-readable field value without exposing secrets."""
    return _normalizar_valor(campo, getattr(instancia, campo.attname, None), instancia)


def _capturar(instancia, nombres):
    resultado = {}
    for campo in instancia._meta.concrete_fields:
        if campo.attname in nombres:
            resultado[campo.attname] = _valor_legible(campo, instancia)
    return resultado


def asignar_actor(actor):
    return _actor_actual.set(actor)


def limpiar_actor(token):
    _actor_actual.reset(token)


@contextmanager
def omitir_auditoria_liquidaciones():
    """Collapse per-unit Liquidacion saves into one administrative event."""
    token = _omitir_auditoria_liquidaciones.set(True)
    try:
        yield
    finally:
        _omitir_auditoria_liquidaciones.reset(token)


def registrar_resumen_liquidacion(periodo, unidades, total_anterior, total_nuevo):
    actor = _actor_actual.get()
    if actor is None or not actor.pk:
        return
    periodo_texto = f"{periodo.mes:02d}/{periodo.anio}"
    nombre = f"Liquidación {periodo_texto} · {periodo.condominio.nombre}"
    peso = lambda valor: f"${valor:,.0f}".replace(",", ".")
    cambios = [
        {"campo": "Alcance", "antes": "—", "despues": f"Todo el condominio · {unidades} unidades"},
        {"campo": "Período", "antes": "—", "despues": periodo_texto},
        {"campo": "Unidades incluidas", "antes": "—", "despues": str(unidades)},
        {
            "campo": "Total liquidado",
            "antes": peso(total_anterior) if total_anterior is not None else "—",
            "despues": peso(total_nuevo),
        },
    ]
    HistorialAdministrativo.objects.create(
        actor=actor,
        condominio_id=periodo.condominio_id,
        accion=HistorialAdministrativo.Accion.CALCULO,
        modelo="Gastos comunes",
        objeto_id=str(periodo.pk),
        objeto=nombre,
        campos=cambios,
    )


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


def _registrar(instancia, accion, cambios):
    actor = _actor_actual.get()
    if actor is None or not actor.pk:
        return
    modelo = instancia._meta
    HistorialAdministrativo.objects.create(
        actor=actor,
        condominio_id=_condominio_id(instancia),
        accion=accion,
        modelo=str(modelo.verbose_name).replace("_", " ").title(),
        objeto_id=str(instancia.pk),
        objeto=f"{modelo.verbose_name} #{instancia.pk}",
        campos=cambios,
    )


_MODELOS_TRACKING = (
    Condominio, Edificio, Unidad, Usuario, AreaComun, HorarioAreaComun,
    Reservacion, Pago, EspacioEstacionamiento, SolicitudBajaArrendatario,
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
    if sender._meta.label_lower == "gastos_comunes.liquidacion" and _omitir_auditoria_liquidaciones.get():
        instance._audit_cambios = []
        return
    if raw or _actor_actual.get() is None:
        instance._audit_cambios = []
        return
    campos = _nombres_campos(sender)
    etiquetas = {
        campo.attname: str(campo.verbose_name).replace("_", " ").capitalize()
        for campo in sender._meta.concrete_fields
    }
    if instance._state.adding:
        instance._audit_cambios = [
            {"campo": etiquetas.get(nombre, nombre), "antes": "—", "despues": _valor_legible(
                sender._meta.get_field(nombre.removesuffix("_id")) if nombre.endswith("_id") else sender._meta.get_field(nombre), instance
            )}
            for nombre in campos
        ]
        return
    valores = sender.objects.filter(pk=instance.pk).values(*campos).first()
    instance._audit_cambios = []
    if valores is not None:
        for nombre in campos:
            if valores.get(nombre) != getattr(instance, nombre):
                campo = sender._meta.get_field(nombre.removesuffix("_id") if nombre.endswith("_id") else nombre)
                instance._audit_cambios.append({
                    "campo": etiquetas.get(nombre, nombre),
                    "antes": _normalizar_valor(campo, valores.get(nombre)),
                    "despues": _valor_legible(campo, instance),
                })


def _despues_de_guardar(sender, instance, created=False, raw=False, **kwargs):
    if raw:
        return
    if sender._meta.label_lower == "gastos_comunes.liquidacion" and _omitir_auditoria_liquidaciones.get():
        return
    cambios = getattr(instance, "_audit_cambios", [])
    if created or cambios:
        _registrar(
            instance,
            HistorialAdministrativo.Accion.CREAR if created else HistorialAdministrativo.Accion.ACTUALIZAR,
            cambios,
        )


def _despues_de_eliminar(sender, instance, **kwargs):
    campos = _nombres_campos(sender)
    etiquetas = {campo.attname: str(campo.verbose_name).replace("_", " ").capitalize() for campo in sender._meta.concrete_fields}
    cambios = [
        {"campo": etiquetas.get(nombre, nombre), "antes": _capturar(instance, [nombre]).get(nombre, "—"), "despues": "—"}
        for nombre in campos
    ]
    _registrar(instance, HistorialAdministrativo.Accion.ELIMINAR, cambios)

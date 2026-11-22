"""
Normaliza los datos ya guardados con las mismas reglas que usan hoy los formularios:
nombres con mayúscula inicial, correos en minúscula, RUT 12345678-5, teléfonos +569...,
patentes ABCD12.

Por defecto solo muestra lo que cambiaría. Para guardar:  python manage.py normalizar_datos --aplicar

Se guarda con .update() para no disparar notificaciones ni llenar el historial administrativo.
"""
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Invitado, Usuario
from core.validators import (
    formatear_rut, normalizar_email, normalizar_nombre_propio, normalizar_patente,
    normalizar_telefono_opcional, validar_rut,
)
from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
from visitas.models import Visita


def _telefono(valor):
    try:
        return normalizar_telefono_opcional(valor)
    except ValidationError:
        return valor  # si no es válido se deja como está; se reporta aparte


class Command(BaseCommand):
    help = "Normaliza nombres, correos, RUT, teléfonos y patentes ya guardados."

    def add_arguments(self, parser):
        parser.add_argument("--aplicar", action="store_true", help="Guarda los cambios (sin esto solo los muestra).")

    def handle(self, *args, aplicar=False, **options):
        self.aplicar = aplicar
        self.total = 0
        with transaction.atomic():
            self._usuarios()
            self._simple(Visita, {"nombre": normalizar_nombre_propio, "patente_vehiculo": normalizar_patente})
            self._simple(Invitado, {"nombre": normalizar_nombre_propio, "patente_vehiculo": normalizar_patente})
            self._simple(ElementoMantenimiento, {"telefono_actual": _telefono, "correo_actual": normalizar_email})
            self._simple(RegistroMantenimiento, {"telefono_empresa": _telefono})
            if not aplicar:
                transaction.set_rollback(True)

        if aplicar:
            self.stdout.write(self.style.SUCCESS(f"Listo: {self.total} registro(s) actualizados."))
        else:
            self.stdout.write(self.style.WARNING(
                f"{self.total} registro(s) cambiarían. No se guardó nada: vuelve a correrlo con --aplicar."
            ))

    def _guardar(self, modelo, obj, cambios):
        self.total += 1
        detalle = ", ".join(f"{c}: {getattr(obj, c)!r} -> {v!r}" for c, v in cambios.items())
        self.stdout.write(f"  {modelo.__name__} #{obj.pk}: {detalle}")
        modelo.objects.filter(pk=obj.pk).update(**cambios)

    def _simple(self, modelo, reglas):
        for obj in modelo.objects.only("pk", *reglas).iterator():
            cambios = {c: f(getattr(obj, c) or "") for c, f in reglas.items()}
            cambios = {c: v for c, v in cambios.items() if v != (getattr(obj, c) or "")}
            if cambios:
                self._guardar(modelo, obj, cambios)

    def _usuarios(self):
        correos_usados = {e.lower(): pk for pk, e in Usuario.objects.values_list("pk", "email")}
        for u in Usuario.objects.select_related("user").iterator():
            cambios = {}
            for campo in ("nombre", "nombre2", "apellido", "apellido2"):
                nuevo = normalizar_nombre_propio(getattr(u, campo))
                if nuevo != getattr(u, campo):
                    cambios[campo] = nuevo

            correo = normalizar_email(u.email)
            if correo != u.email:
                if correos_usados.get(correo, u.pk) != u.pk:
                    self.stdout.write(self.style.ERROR(
                        f"  Usuario #{u.pk}: no se cambia el correo {u.email!r}: otro usuario ya usa {correo!r}."
                    ))
                else:
                    cambios["email"] = correo

            if u.rut:
                try:
                    validar_rut(u.rut)
                    rut = formatear_rut(u.rut)
                    if rut != u.rut:
                        cambios["rut"] = rut
                except ValidationError:
                    self.stdout.write(self.style.ERROR(f"  Usuario #{u.pk}: RUT inválido {u.rut!r}, revísalo a mano."))

            if u.telefono:
                telefono = _telefono(u.telefono)
                if telefono == u.telefono and not telefono.startswith("+"):
                    self.stdout.write(self.style.ERROR(f"  Usuario #{u.pk}: teléfono inválido {u.telefono!r}, revísalo a mano."))
                elif telefono != u.telefono and len(telefono) <= 15:
                    cambios["telefono"] = telefono

            if cambios:
                self._guardar(Usuario, u, cambios)
                if "email" in cambios and u.user_id and u.user.email != cambios["email"]:
                    type(u.user).objects.filter(pk=u.user_id).update(email=cambios["email"])

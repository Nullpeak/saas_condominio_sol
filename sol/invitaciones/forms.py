import re

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.models import Condominio, Usuario
from core.validators import formatear_telefono, validar_email, validar_telefono
from .permisos import PUEDE_INVITAR, Rol

MAX_CORREOS = 50


class InvitarForm(forms.Form):
    emails = forms.CharField(
        label="Correos",
        help_text="Uno por línea, o separados por coma.",
        widget=forms.Textarea(attrs={"rows": 5}),
    )
    rol = forms.ChoiceField(label="Rol")
    condominio = forms.ModelChoiceField(
        label="Condominio",
        queryset=Condominio.objects.filter(activo=True),
        required=False,
    )

    def __init__(self, *args, perfil, **kwargs):
        super().__init__(*args, **kwargs)
        self.perfil = perfil
        permitidos = PUEDE_INVITAR[perfil.rol]
        self.fields["rol"].choices = [(v, l.capitalize()) for v, l in Usuario.Rol.choices if v in permitidos]
        if perfil.rol != Rol.SUPER:
            del self.fields["condominio"]  # los demás solo invitan a su propio condominio

    def clean_emails(self):
        crudos = [e.strip().lower() for e in re.split(r"[,;\s]+", self.cleaned_data["emails"]) if e.strip()]
        unicos = list(dict.fromkeys(crudos))
        if not unicos:
            raise ValidationError("Ingresa al menos un correo.")
        if len(unicos) > MAX_CORREOS:
            raise ValidationError(f"Máximo {MAX_CORREOS} correos por envío.")
        invalidos = []
        for e in unicos:
            try:
                validar_email(e)
                if len(e) > 50:
                    raise ValidationError("largo")
            except ValidationError:
                invalidos.append(e)
        if invalidos:
            raise ValidationError("Correos no válidos: " + ", ".join(invalidos))
        return unicos

    def clean_rol(self):
        rol = self.cleaned_data["rol"]
        if rol not in PUEDE_INVITAR[self.perfil.rol]:  # defensa extra por si manipulan el POST
            raise ValidationError("No puedes invitar a ese rol.")
        return rol

    def clean(self):
        data = super().clean()
        if self.perfil.rol == Rol.SUPER:
            condominio = data.get("condominio")
            if not condominio and "condominio" not in self.errors:
                self.add_error("condominio", "Selecciona el condominio.")
        else:
            condominio = self.perfil.condominio
            if condominio is None:
                raise ValidationError("Tu cuenta no tiene un condominio asignado.")
        data["condominio_final"] = condominio
        return data


class AceptarInvitacionForm(forms.ModelForm):
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Repite la contraseña", widget=forms.PasswordInput)

    class Meta:
        model = Usuario
        fields = ["nombre", "nombre2", "apellido", "apellido2", "rut", "telefono"]

    def __init__(self, *args, invitacion, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitacion = invitacion
        self.fields["rut"].required = True  # igual que en tu vista usuario_crear

    def clean_telefono(self):
        telefono = self.cleaned_data.get("telefono", "").strip()
        if not telefono:
            return ""
        validar_telefono(telefono)
        return formatear_telefono(telefono)

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Las contraseñas no coinciden.")
            else:
                User = get_user_model()
                try:
                    validate_password(p1, User(username=self.invitacion.email, email=self.invitacion.email))
                except ValidationError as e:
                    self.add_error("password1", e)
        return data
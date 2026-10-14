from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password

from core.validators import (
    formatear_rut,
    formatear_telefono,
    validar_email,
    validar_rut,
    validar_telefono,
)

from .ciudades import ciudades_chile


class SuperUsuarioForm(forms.Form):
    username = forms.CharField(label="Nombre de usuario", max_length=150)
    nombre = forms.CharField(label="Nombre", max_length=30)
    nombre2 = forms.CharField(label="Segundo nombre (opcional)", max_length=30, required=False)
    apellido = forms.CharField(label="Apellido", max_length=30)
    apellido2 = forms.CharField(label="Segundo apellido (opcional)", max_length=30, required=False)
    email = forms.EmailField(label="Correo electrónico", max_length=50, validators=[validar_email])
    rut = forms.CharField(label="RUT", max_length=12, validators=[validar_rut])
    telefono = forms.CharField(
        label="Teléfono",
        max_length=20,
        validators=[validar_telefono],
        widget=forms.TextInput(
            attrs={"placeholder": "+56 9 1234 5678", "inputmode": "tel", "autocomplete": "tel"}
        ),
    )
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Repite la contraseña", widget=forms.PasswordInput)

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Ese nombre de usuario ya existe.")
        return username

    def clean_rut(self):
        # Mismo formato con el que Usuario.save() lo guarda: 12345678-5
        return formatear_rut(self.cleaned_data["rut"])

    def clean_telefono(self):
        # Se guarda normalizado: +56912345678
        telefono = formatear_telefono(self.cleaned_data["telefono"])
        if len(telefono) > 15:  # largo máximo de Usuario.telefono
            raise forms.ValidationError("El teléfono es demasiado largo.")
        return telefono

    def clean(self):
        cleaned = super().clean()
        p1, p2 = cleaned.get("password1"), cleaned.get("password2")
        if p1 and p2:
            if p1 != p2:
                self.add_error("password2", "Las contraseñas no coinciden.")
            else:
                try:
                    validate_password(p1)
                except forms.ValidationError as e:
                    self.add_error("password1", e)
        return cleaned


class CondominioForm(forms.Form):
    nombre = forms.CharField(label="Nombre del condominio", max_length=150)
    direccion = forms.CharField(label="Dirección", max_length=255)
    ciudad = forms.CharField(
        label="Ciudad",
        max_length=50,
        widget=forms.TextInput(attrs={"list": "lista-ciudades", "autocomplete": "off"}),
    )
    edificios = forms.IntegerField(label="Cantidad de edificios", min_value=1, initial=1)
    numero_pisos = forms.IntegerField(label="Pisos por edificio", min_value=2, max_value=20)
    viviendas_por_piso = forms.IntegerField(label="Viviendas por piso", min_value=1)
    portada = forms.ImageField(label="Imagen de portada (opcional)", required=False)

    # Sugerencias para el campo ciudad (el template las dibuja como <datalist>).
    ciudades_sugeridas = ciudades_chile()

    def clean_ciudad(self):
        # Si coincide con una ciudad de la lista, se usa su nombre exacto;
        # si no, se acepta lo escrito (comunas que la lista no incluye).
        escrito = " ".join(self.cleaned_data["ciudad"].split())
        por_nombre = {n.lower(): n for n in self.ciudades_sugeridas}
        return por_nombre.get(escrito.lower(), escrito.title())
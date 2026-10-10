import re
from operator import attrgetter

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from core.models import Condominio, Edificio, Unidad, Usuario
from core.validators import formatear_telefono, validar_email, validar_rut, validar_telefono
from .models import Invitacion
from .permisos import PUEDE_INVITAR, Rol

MAX_CORREOS = 50
MAX_PERSONAS_UNIDAD = 20
USERNAME_RE = re.compile(r"[a-z0-9._-]{3,30}")
RELACION_TEXTO = {"dueno": "dueño", "arrendatario": "arrendatario"}


class SelectFiltrable(forms.Select):
    """
    Select que agrega atributos data-* a cada opción (por ejemplo data-edificio),
    para que el JavaScript de la página pueda filtrar las opciones sin recargar.
    """

    def __init__(self, *args, data_attrs=None, **kwargs):
        super().__init__(*args, **kwargs)
        # {"data-edificio": "edificio_id"} -> lee instancia.edificio_id (admite rutas con punto)
        self.data_attrs = {nombre: attrgetter(ruta) for nombre, ruta in (data_attrs or {}).items()}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        instancia = getattr(value, "instance", None)  # las opciones vacías no tienen instancia
        if instancia is not None:
            for nombre, leer in self.data_attrs.items():
                opcion["attrs"][nombre] = leer(instancia)
        return opcion


class InvitarForm(forms.Form):
    emails = forms.CharField(
        label="Correos",
        help_text="Uno por línea, o separados por coma. Para residentes, de a una persona.",
        widget=forms.Textarea(attrs={"rows": 4}),
    )
    rol = forms.ChoiceField(label="Rol")
    condominio = forms.ModelChoiceField(
        label="Condominio",
        queryset=Condominio.objects.filter(activo=True),
        required=False,
    )
    edificio = forms.ModelChoiceField(
        label="Edificio",
        queryset=Edificio.objects.none(),
        required=False,
        empty_label="Todos los edificios",
        help_text="Filtra la lista de unidades.",
        widget=SelectFiltrable(data_attrs={"data-condominio": "condominio_id"}),
    )
    unidad = forms.ModelChoiceField(
        label="Unidad",
        queryset=Unidad.objects.none(),
        required=False,
        empty_label="Selecciona una unidad",
        widget=SelectFiltrable(data_attrs={
            "data-edificio": "edificio_id",
            "data-condominio": "edificio.condominio_id",
        }),
    )
    relacion = forms.ChoiceField(
        label="Calidad en la unidad",
        choices=[("", "---------")] + list(Invitacion.Relacion.choices),
        required=False,
    )

    def __init__(self, *args, perfil, **kwargs):
        super().__init__(*args, **kwargs)
        self.perfil = perfil
        self.es_super = perfil.rol == Rol.SUPER
        permitidos = PUEDE_INVITAR[perfil.rol]
        self.fields["rol"].choices = [(v, l.capitalize()) for v, l in Usuario.Rol.choices if v in permitidos]

        unidades = Unidad.objects.select_related("edificio__condominio").order_by(
            "edificio__condominio__nombre", "edificio__numero", "piso", "posicion"
        )
        # Solo edificios que ya tienen unidades generadas
        edificios = (
            Edificio.objects.select_related("condominio")
            .filter(unidades__isnull=False)
            .distinct()
            .order_by("condominio__nombre", "numero")
        )
        if not self.es_super:
            del self.fields["condominio"]  # los demás solo invitan a su propio condominio
            unidades = unidades.filter(edificio__condominio=perfil.condominio)
            edificios = edificios.filter(condominio=perfil.condominio)
        self.fields["unidad"].queryset = unidades
        self.fields["edificio"].queryset = edificios
        self.fields["edificio"].label_from_instance = self._etiqueta_edificio
        self.fields["unidad"].label_from_instance = self._etiqueta_unidad

    def _etiqueta_edificio(self, e):
        return f"{e.condominio} · {e.nombre}" if self.es_super else e.nombre

    def _etiqueta_unidad(self, u):
        texto = f"{u.edificio.nombre} · Unidad {u.numero_unidad}"
        if self.es_super:
            texto = f"{u.edificio.condominio} · {texto}"
        if u.dueno_id:
            texto += " (con dueño)"
        if u.arrendatario_id:
            texto += " (con arrendatario)"
        return texto

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

        # --- condominio ---
        if self.es_super:
            condominio = data.get("condominio")
            if not condominio and "condominio" not in self.errors:
                self.add_error("condominio", "Selecciona el condominio.")
        else:
            condominio = self.perfil.condominio
            if condominio is None:
                raise ValidationError("Tu cuenta no tiene un condominio asignado.")
        data["condominio_final"] = condominio

        # --- unidad (solo residentes) ---
        if data.get("rol") != Rol.RESIDENTE:
            data["unidad"], data["relacion"] = None, ""
            return data

        emails = data.get("emails") or []
        unidad, relacion = data.get("unidad"), data.get("relacion")

        if len(emails) > 1:
            self.add_error("emails", "Para residentes invita de a una persona: cada uno va a una unidad distinta.")
        if not unidad and "unidad" not in self.errors:
            self.add_error("unidad", "Selecciona la unidad donde vivirá.")
        if not relacion:
            self.add_error("relacion", "Indica si será dueño o arrendatario.")

        edificio = data.get("edificio")
        if unidad and edificio and unidad.edificio_id != edificio.pk:
            self.add_error("unidad", "Esa unidad no pertenece al edificio seleccionado.")
        elif unidad and condominio and unidad.edificio.condominio_id != condominio.pk:
            self.add_error("unidad", "Esa unidad no pertenece al condominio seleccionado.")
        elif unidad and relacion:
            texto = RELACION_TEXTO[relacion]
            if getattr(unidad, f"{relacion}_id"):
                self.add_error("unidad", f"Esa unidad ya tiene un {texto} asignado.")
            else:
                pendientes = Invitacion.vigentes().filter(unidad=unidad, relacion=relacion)
                if emails:
                    pendientes = pendientes.exclude(email=emails[0])  # reenviar al mismo correo está permitido
                if pendientes.exists():
                    self.add_error("unidad", f"Esa unidad ya tiene una invitación pendiente como {texto}.")
        return data


class AceptarInvitacionForm(forms.ModelForm):
    username = forms.CharField(
        label="Nombre de usuario",
        max_length=150,
        help_text="Entre 3 y 30 caracteres: letras minúsculas, números, punto, guion o guion bajo.",
    )
    password1 = forms.CharField(label="Contraseña", widget=forms.PasswordInput)
    password2 = forms.CharField(label="Repite la contraseña", widget=forms.PasswordInput)
    cantidad_residentes = forms.IntegerField(
        label="¿Cuántas personas viven o vivirán en la unidad?",
        min_value=1,
        max_value=MAX_PERSONAS_UNIDAD,
        help_text="Incluyéndote a ti.",
    )

    field_order = [
        "username", "nombre", "nombre2", "apellido", "apellido2",
        "rut", "telefono", "cantidad_residentes", "password1", "password2",
    ]

    class Meta:
        model = Usuario
        fields = ["nombre", "nombre2", "apellido", "apellido2", "rut", "telefono"]

    def __init__(self, *args, invitacion, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitacion = invitacion
        self.fields["rut"].required = True  # igual que en tu vista usuario_crear
        if not invitacion.unidad_id:
            del self.fields["cantidad_residentes"]  # solo aplica a residentes

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        es_su_correo = username == self.invitacion.email.lower()

        if not es_su_correo and not USERNAME_RE.fullmatch(username):
            raise ValidationError(
                "Usa entre 3 y 30 caracteres: letras minúsculas, números, punto, guion o guion bajo."
            )
        # Un username con forma de RUT permitiría bloquear el login por RUT de otra persona
        try:
            validar_rut(username)
        except ValidationError:
            pass
        else:
            raise ValidationError("El nombre de usuario no puede ser un RUT.")

        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("Ese nombre de usuario ya está en uso.")
        return username

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
                    validate_password(p1, User(username=data.get("username") or self.invitacion.email,
                                               email=self.invitacion.email))
                except ValidationError as e:
                    self.add_error("password1", e)
        return data
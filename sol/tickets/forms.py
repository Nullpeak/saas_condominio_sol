import os
from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from PIL import Image

from core.models import Condominio, Unidad
from core.validators import formatear_rut, validar_rut
from .models import Ticket
from .validators import EXTENSIONES_VIDEO, MAX_ADJUNTO_BYTES, validar_adjunto
from .permisos import Rol, asignables, estados_siguientes

MAX_ARCHIVOS = 5
MAX_MB = 10
EXT_IMAGEN = {".jpg", ".jpeg", ".png", ".webp"}
EXT_VIDEO = EXTENSIONES_VIDEO
EXT_PERMITIDAS = EXT_IMAGEN | {".pdf"} | EXT_VIDEO
MAX_MB_VIDEO = MAX_ADJUNTO_BYTES // (1024 * 1024)


# ------------------------------------------------------------ adjuntos
class MultiArchivoInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultiArchivoField(forms.FileField):
    """Varios archivos en un solo input (patrón de la documentación de Django)."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultiArchivoInput(attrs={"accept": ",".join(sorted(EXT_PERMITIDAS))}))
        kwargs.setdefault("required", False)
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        if not data:
            return []
        archivos = data if isinstance(data, (list, tuple)) else [data]
        if len(archivos) > MAX_ARCHIVOS:
            raise ValidationError(f"Máximo {MAX_ARCHIVOS} archivos.")
        limpios = []
        for f in archivos:
            f = super().clean(f, initial)
            ext = os.path.splitext(f.name)[1].lower()
            if ext not in EXT_PERMITIDAS:
                raise ValidationError(f"«{f.name}»: solo se aceptan fotos (JPG, PNG, WEBP), PDF o videos.")
            if ext in EXT_VIDEO:
                try:
                    validar_adjunto(f)  # tamaño y cabecera del video
                except ValidationError as e:
                    raise ValidationError(f"«{f.name}»: {' '.join(e.messages)}")
                limpios.append(f)
                continue
            if f.size > MAX_MB * 1024 * 1024:
                raise ValidationError(f"«{f.name}» pesa más de {MAX_MB} MB.")
            if ext in EXT_IMAGEN:
                try:
                    Image.open(f).verify()  # que sea de verdad una imagen
                except Exception:
                    raise ValidationError(f"«{f.name}» no es una imagen válida.")
                f.seek(0)
            elif f.read(5) != b"%PDF-":
                raise ValidationError(f"«{f.name}» no es un PDF válido.")
            else:
                f.seek(0)
            limpios.append(f)
        return limpios


ARCHIVOS_HELP = (
    f"Opcional. Hasta {MAX_ARCHIVOS} archivos: fotos o PDF de máximo {MAX_MB} MB, "
    f"o videos de máximo {MAX_MB_VIDEO} MB."
)


# ------------------------------------------------------------ crear
class TicketForm(forms.Form):
    condominio = forms.ModelChoiceField(
        label="Condominio", queryset=Condominio.objects.filter(activo=True), required=False
    )
    titulo = forms.CharField(
        label="Asunto", max_length=150,
        widget=forms.TextInput(attrs={"placeholder": "Ej: Filtración en el baño"}),
    )
    categoria = forms.ChoiceField(label="Categoría", choices=Ticket.Categoria.choices)
    unidad = forms.ModelChoiceField(
        label="Unidad", queryset=Unidad.objects.none(), required=False, empty_label="No aplica / área común"
    )
    descripcion = forms.CharField(
        label="Describe el problema",
        widget=forms.Textarea(attrs={"rows": 6, "placeholder": "¿Qué pasó, dónde y desde cuándo?"}),
    )
    archivos = MultiArchivoField(label="Fotos o documentos", help_text=ARCHIVOS_HELP)

    def __init__(self, *args, perfil, **kwargs):
        super().__init__(*args, **kwargs)
        self.perfil = perfil
        self.es_super = perfil.rol == Rol.SUPER
        unidades = Unidad.objects.select_related("edificio__condominio").order_by(
            "edificio__condominio__nombre", "edificio__numero", "piso", "posicion"
        )
        if perfil.rol == Rol.RESIDENTE:
            unidades = unidades.filter(Q(dueno=perfil) | Q(arrendatario=perfil)).distinct()
            if unidades.count() == 1:
                self.fields["unidad"].initial = unidades.first()
        elif not self.es_super:
            unidades = unidades.filter(edificio__condominio__in=perfil.condominios_asignados())
        self.fields["unidad"].queryset = unidades

        # El selector de condominio aparece para el super y para quien administra varios
        condominios = perfil.condominios_asignados().filter(activo=True) if perfil.rol != Rol.RESIDENTE else None
        self.varios_condominios = condominios is not None and condominios.count() > 1
        self.fields["unidad"].label_from_instance = self._etiqueta_unidad
        if self.varios_condominios:
            self.fields["condominio"].queryset = condominios.order_by("nombre")
            self.fields["condominio"].required = True
            self.fields["condominio"].initial = perfil.condominio_id
        else:
            del self.fields["condominio"]

    def _etiqueta_unidad(self, u):
        texto = f"{u.edificio.nombre} · Unidad {u.numero_unidad}"
        return f"{u.edificio.condominio} · {texto}" if self.varios_condominios else texto

    def es_de_visitas(self):
        """Para la plantilla y la vista: ¿el ticket que se está creando es de visitas?"""
        valor = self["categoria"].value()
        return valor == Ticket.Categoria.VISITAS

    def clean(self):
        data = super().clean()
        if data.get("categoria") == Ticket.Categoria.VISITAS and not data.get("unidad") \
                and "unidad" not in self.errors:
            self.add_error("unidad", "Indica la unidad que recibirá a las visitas.")
        unidad = data.get("unidad")
        if self.varios_condominios:
            condominio = data.get("condominio")
        elif unidad:
            condominio = unidad.edificio.condominio
        else:
            condominio = self.perfil.condominio or self.perfil.condominios_asignados().first()
        if condominio is None and not self.errors:
            raise ValidationError("Tu cuenta no tiene un condominio asignado.")
        if unidad and condominio and unidad.edificio.condominio_id != condominio.pk:
            self.add_error("unidad", "Esa unidad no pertenece al condominio seleccionado.")
        data["condominio_final"] = condominio
        return data


class EditarTicketForm(forms.ModelForm):
    """Lo que el residente puede corregir mientras el ticket sigue abierto."""

    class Meta:
        model = Ticket
        fields = ["titulo", "categoria", "descripcion"]
        labels = {"titulo": "Asunto", "descripcion": "Describe el problema"}
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.categoria == Ticket.Categoria.VISITAS:
            # Las visitas ya pedidas dependen de esta categoría: no se puede cambiar
            del self.fields["categoria"]
        else:
            self.fields["categoria"].choices = [
                c for c in Ticket.Categoria.choices if c[0] != Ticket.Categoria.VISITAS
            ]


# ------------------------------------------------------------ visitas
MAX_VISITAS = 10
MAX_DIAS_VISITA = 30
FORMATO_FECHA = "%Y-%m-%dT%H:%M"


def _fecha_hora(label):
    return forms.DateTimeField(
        label=label,
        input_formats=[FORMATO_FECHA],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=FORMATO_FECHA),
    )


class VisitaForm(forms.Form):
    nombre = forms.CharField(label="Nombre completo", max_length=100)
    rut = forms.CharField(label="RUT", max_length=12, required=False)
    patente = forms.CharField(label="Patente", max_length=10, required=False,
                              widget=forms.TextInput(attrs={"placeholder": "Si viene en auto"}))
    llegada = _fecha_hora("Llega")
    salida = _fecha_hora("Se va")

    def clean_rut(self):
        rut = self.cleaned_data["rut"].strip()
        if not rut:
            return ""
        validar_rut(rut)
        return formatear_rut(rut)

    def clean_patente(self):
        return self.cleaned_data["patente"].replace(" ", "").replace("-", "").upper()

    def clean(self):
        data = super().clean()
        llegada, salida = data.get("llegada"), data.get("salida")
        if llegada and salida:
            if salida <= llegada:
                self.add_error("salida", "Debe ser posterior a la llegada.")
            elif salida - llegada > timedelta(days=MAX_DIAS_VISITA):
                self.add_error("salida", f"Una visita no puede durar más de {MAX_DIAS_VISITA} días.")
        if llegada and llegada < timezone.now() - timedelta(hours=1):
            self.add_error("llegada", "La llegada no puede estar en el pasado.")
        return data


VisitaFormSet = forms.formset_factory(
    VisitaForm, extra=0, min_num=1, validate_min=True, max_num=MAX_VISITAS, validate_max=True
)


# ------------------------------------------------------------ comentar
class ComentarioForm(forms.Form):
    texto = forms.CharField(
        label="Mensaje",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Escribe una respuesta..."}),
    )
    interno = forms.BooleanField(
        label="Nota interna (solo la ve el equipo de administración)", required=False
    )
    archivos = MultiArchivoField(label="Adjuntar", help_text=ARCHIVOS_HELP)

    def __init__(self, *args, staff=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not staff:
            del self.fields["interno"]


# ------------------------------------------------------------ gestionar (staff)
class GestionForm(forms.Form):
    estado = forms.ChoiceField(label="Estado")
    prioridad = forms.ChoiceField(
        label="Prioridad", required=False, choices=[("", "Sin prioridad")] + list(Ticket.Prioridad.choices)
    )
    categoria = forms.ChoiceField(label="Categoría", choices=Ticket.Categoria.choices)
    asignado = forms.ModelChoiceField(label="Responsable", queryset=None, required=False,
                                      empty_label="Sin asignar")

    def __init__(self, *args, perfil, ticket, **kwargs):
        kwargs.setdefault("initial", {
            "estado": ticket.estado, "prioridad": ticket.prioridad,
            "categoria": ticket.categoria, "asignado": ticket.asignado_id,
        })
        super().__init__(*args, **kwargs)
        self.ticket = ticket
        permitidos = {ticket.estado} | estados_siguientes(perfil, ticket)
        self.fields["estado"].choices = [(v, l) for v, l in Ticket.Estado.choices if v in permitidos]
        self.fields["asignado"].queryset = asignables(ticket.condominio_id)
        self.fields["asignado"].label_from_instance = lambda u: f"{u} ({u.get_rol_display()})"

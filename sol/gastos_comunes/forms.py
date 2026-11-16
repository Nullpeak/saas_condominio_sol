from django import forms
from core.models import Condominio, Edificio, Unidad
from .models import Periodo, ItemGasto, PagoRegistrado


class PeriodoForm(forms.ModelForm):
    class Meta:
        model = Periodo
        fields = ["condominio", "anio", "mes", "vencimiento", "recargo_reserva"]
        widgets = {"vencimiento": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, condominios=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condominio"].widget.attrs["class"] = "form-select"
        self.fields["anio"].label = "Año"
        self.fields["anio"].help_text = "Escribe el año completo, por ejemplo 2026."
        self.fields["anio"].widget.attrs.update({"class": "form-control", "min": "2000", "max": "2100", "step": "1"})
        self.fields["mes"].label = "Mes"
        self.fields["mes"].help_text = "Número de mes: 1 para enero y 12 para diciembre."
        self.fields["mes"].widget.attrs.update({"class": "form-control", "min": "1", "max": "12", "step": "1"})
        self.fields["vencimiento"].label = "Fecha de vencimiento"
        self.fields["vencimiento"].help_text = "Último día para pagar antes de marcar saldo como vencido."
        self.fields["vencimiento"].widget.attrs["class"] = "form-control"
        self.fields["recargo_reserva"].label = "Fondo de reserva (%)"
        self.fields["recargo_reserva"].help_text = "Porcentaje aplicado a la parte de los gastos ordinarios compartidos; mínimo 5%."
        self.fields["recargo_reserva"].widget.attrs.update({"class": "form-control", "min": "5", "step": "0.01"})
        if condominios is not None:
            self.fields["condominio"].queryset = condominios


class ItemGastoForm(forms.ModelForm):
    class Meta:
        model = ItemGasto
        fields = ["concepto", "tipo", "alcance", "edificio", "unidad", "monto", "respaldo"]
        widgets = {
            "concepto": forms.TextInput(attrs={"class": "form-control", "placeholder": "Ej.: mantención del ascensor"}),
            "tipo": forms.Select(attrs={"class": "form-select"}),
            "alcance": forms.Select(attrs={"class": "form-select"}),
            "edificio": forms.Select(attrs={"class": "form-select"}),
            "unidad": forms.Select(attrs={"class": "form-select"}),
            "monto": forms.NumberInput(attrs={"class": "form-control", "min": "1", "step": "1", "placeholder": "Monto total de la factura"}),
            "respaldo": forms.ClearableFileInput(attrs={"class": "form-control", "accept": ".pdf,.jpg,.jpeg,.png"}),
        }

    def __init__(self, *args, periodo=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["concepto"].label = "Concepto del gasto"
        self.fields["concepto"].help_text = "Qué servicio o trabajo se pagó."
        self.fields["tipo"].label = "Clasificación"
        self.fields["tipo"].help_text = "Habitual o extraordinario aprobado."
        self.fields["alcance"].label = "¿A quién corresponde este gasto?"
        self.fields["alcance"].help_text = "Quién comparte el gasto."
        self.fields["edificio"].label = "Edificio"
        self.fields["edificio"].help_text = "Se reparte entre las unidades del edificio."
        self.fields["unidad"].label = "Unidad"
        self.fields["unidad"].help_text = "Se carga solo a esta unidad."
        self.fields["monto"].label = "Monto total de la boleta o factura ($)"
        self.fields["monto"].help_text = "Ingresa el total exacto en pesos, sin puntos de miles. El sistema calcula la parte que corresponde a cada unidad."
        self.fields["respaldo"].label = "Boleta o factura de respaldo (opcional)"
        self.fields["respaldo"].help_text = "Comprobante opcional."
        if periodo:
            self.fields["edificio"].queryset = Edificio.objects.filter(condominio=periodo.condominio)
            self.fields["unidad"].queryset = Unidad.objects.filter(edificio__condominio=periodo.condominio)

    def clean(self):
        cleaned = super().clean()
        alcance = cleaned.get("alcance")
        if alcance == ItemGasto.Alcance.CONDOMINIO:
            cleaned["edificio"] = None
            cleaned["unidad"] = None
        elif alcance == ItemGasto.Alcance.EDIFICIO:
            cleaned["unidad"] = None
        elif alcance == ItemGasto.Alcance.UNIDAD:
            cleaned["edificio"] = None
        return cleaned


class PagoForm(forms.ModelForm):
    class Meta:
        model = PagoRegistrado
        fields = ["monto", "fecha", "metodo", "referencia"]
        widgets = {"fecha": forms.DateInput(attrs={"type": "date"})}

from django.contrib import admin
from .models import DetalleCargo, ItemGasto, LecturaMedidor, Liquidacion, PagoOnline, PagoRegistrado, Periodo, TasaInteresCorriente


class GastoInline(admin.TabularInline):
    model = ItemGasto
    extra = 0


@admin.register(Periodo)
class PeriodoAdmin(admin.ModelAdmin):
    list_display = ("condominio", "anio", "mes", "estado", "vencimiento")
    list_filter = ("estado", "anio", "condominio")
    inlines = (GastoInline,)


admin.site.register((LecturaMedidor, Liquidacion, DetalleCargo, PagoRegistrado, PagoOnline))
@admin.register(TasaInteresCorriente)
class TasaInteresCorrienteAdmin(admin.ModelAdmin):
    list_display = ("certificado", "segmento", "tasa_anual", "vigente_desde", "vigente_hasta")
    list_filter = ("segmento",)

from django.contrib import admin

# Register your models here.

from .models import (
    Condominio,
    Edificio,
    Unidad,
    Ciudad,
    Usuario,
    Costo,
    AreaComun,
    Pago,
    Reservacion,
    Invitado,
    Anuncio,
    EspacioEstacionamiento,
    SolicitudBajaArrendatario,
)

admin.site.register(Condominio)
admin.site.register(Edificio)
admin.site.register(Unidad)
admin.site.register(Ciudad)
admin.site.register(Usuario)
admin.site.register(Costo)
admin.site.register(AreaComun)
admin.site.register(Pago)
admin.site.register(Reservacion)
admin.site.register(Invitado)
admin.site.register(Anuncio)
admin.site.register(EspacioEstacionamiento)

@admin.register(SolicitudBajaArrendatario)
class SolicitudBajaArrendatarioAdmin(admin.ModelAdmin):
    list_display = ("unidad", "arrendatario", "solicitante", "estado", "creado")
    list_filter = ("estado",)

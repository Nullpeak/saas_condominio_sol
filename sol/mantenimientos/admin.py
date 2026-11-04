from django.contrib import admin

from .models import DocumentoMantenimiento, ElementoMantenimiento, RegistroMantenimiento


class DocumentoMantenimientoInline(admin.TabularInline):
    model = DocumentoMantenimiento
    extra = 0


@admin.register(ElementoMantenimiento)
class ElementoMantenimientoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "categoria", "condominio", "empresa_actual", "telefono_actual")
    list_filter = ("condominio", "categoria")
    search_fields = ("nombre", "categoria", "empresa_actual", "condominio__nombre")


@admin.register(RegistroMantenimiento)
class RegistroMantenimientoAdmin(admin.ModelAdmin):
    list_display = ("elemento", "fecha", "empresa", "proximo_mantenimiento", "creado_por")
    list_filter = ("fecha", "elemento__categoria", "elemento__condominio")
    search_fields = ("elemento__nombre", "elemento__condominio__nombre", "empresa", "descripcion")
    inlines = (DocumentoMantenimientoInline,)


@admin.register(DocumentoMantenimiento)
class DocumentoMantenimientoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "tipo", "registro", "subido")
    list_filter = ("tipo", "registro__elemento__condominio")

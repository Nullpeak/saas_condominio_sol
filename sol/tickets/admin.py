from django.contrib import admin
from .models import Ticket, TicketArchivo


class TicketArchivoInline(admin.TabularInline):
    model = TicketArchivo
    extra = 0


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "titulo", "estado", "condominio", "unidad", "usuario", "asignado", "resuelto_por", "creado")
    list_filter = ("estado", "prioridad", "condominio")
    search_fields = ("titulo", "descripcion", "usuario__nombre", "usuario__apellido")
    inlines = (TicketArchivoInline,)


admin.site.register(TicketArchivo)

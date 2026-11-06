from django.contrib import admin

from .models import Ticket, TicketArchivo, TicketComentario, TicketEvento, TicketVisita


class ComentarioInline(admin.TabularInline):
    model = TicketComentario
    extra = 0
    readonly_fields = ("creado",)


class ArchivoInline(admin.TabularInline):
    model = TicketArchivo
    extra = 0
    readonly_fields = ("subido",)


class VisitaInline(admin.TabularInline):
    model = TicketVisita
    extra = 0
    readonly_fields = ("revisada_por", "revisada", "visita")


class EventoInline(admin.TabularInline):
    model = TicketEvento
    extra = 0
    can_delete = False
    readonly_fields = ("autor", "tipo", "anterior", "nuevo", "creado")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "titulo", "condominio", "categoria", "estado", "prioridad", "asignado", "creado")
    list_filter = ("estado", "prioridad", "categoria", "condominio")
    search_fields = ("titulo", "descripcion")
    raw_id_fields = ("usuario", "asignado", "unidad")
    inlines = [VisitaInline, ComentarioInline, ArchivoInline, EventoInline]

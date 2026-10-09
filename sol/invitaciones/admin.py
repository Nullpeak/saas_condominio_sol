from django.contrib import admin

from .models import Invitacion


@admin.register(Invitacion)
class InvitacionAdmin(admin.ModelAdmin):
    list_display = ("email", "rol", "condominio", "invitado_por", "creada", "expira", "usada", "revocada")
    list_filter = ("rol", "revocada")
    search_fields = ("email",)
    readonly_fields = ("token_hash",)
from django.contrib import admin

# Register your models here.

from .models import (
    Condominio,
    Edificio,
    Unidad,
    Rol,
    Ciudad,
    Usuario,
    Costo,
    AreaComun,
    Pago,
    Reservacion,
    Invitado,
    Ticket,
    TicketArchivo,
    Anuncio,
    EspacioEstacionamiento,
)

admin.site.register(Condominio)
admin.site.register(Edificio)
admin.site.register(Unidad)
admin.site.register(Rol)
admin.site.register(Ciudad)
admin.site.register(Usuario)
admin.site.register(Costo)
admin.site.register(AreaComun)
admin.site.register(Pago)
admin.site.register(Reservacion)
admin.site.register(Invitado)
admin.site.register(Ticket)
admin.site.register(TicketArchivo)
admin.site.register(Anuncio)
admin.site.register(EspacioEstacionamiento)
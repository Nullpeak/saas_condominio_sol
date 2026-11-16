from django.contrib import admin

from .models import AnuncioOficial, AnuncioResidente, LecturaAnuncioOficial, LecturaAnuncioResidente


@admin.register(AnuncioOficial)
class AnuncioOficialAdmin(admin.ModelAdmin):
    list_display = ("titulo", "condominio", "autor", "alcance", "creado")
    list_filter = ("condominio", "alcance", "creado")
    search_fields = ("titulo", "descripcion")


@admin.register(AnuncioResidente)
class AnuncioResidenteAdmin(admin.ModelAdmin):
    list_display = ("titulo", "condominio", "unidad", "autor", "estado", "creado")
    list_filter = ("condominio", "estado", "alcance", "creado")
    search_fields = ("titulo", "descripcion", "autor__nombre", "autor__apellido")
    readonly_fields = ("creado", "moderado")


admin.site.register(LecturaAnuncioOficial)
admin.site.register(LecturaAnuncioResidente)

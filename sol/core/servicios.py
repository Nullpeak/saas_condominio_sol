"""
Lógica compartida que usan varias pantallas.

crear_condominio() es la única forma de crear un condominio: la usan el launch (primer
condominio) y la pantalla "Crear condominio". Así los dos crean exactamente lo mismo:
condominio, edificios y todas sus unidades.
"""
from django.db import transaction

from launch.ciudades import ciudades_chile

from .models import Ciudad, Condominio


@transaction.atomic
def crear_condominio(datos):
    """
    `datos` es el cleaned_data de launch.forms.CondominioForm:
    nombre, direccion, ciudad, edificios, numero_pisos, viviendas_por_piso,
    portada (opcional) y estimado_personas_departamento (opcional).
    """
    # La tabla Ciudad se completa con las ciudades de Chile; si escribieron una que
    # no está en la lista (una comuna, por ejemplo), se crea.
    Ciudad.objects.bulk_create([Ciudad(nombre=n) for n in ciudades_chile()], ignore_conflicts=True)
    ciudad = Ciudad.objects.filter(nombre__iexact=datos["ciudad"]).first()
    if ciudad is None:
        ciudad = Ciudad.objects.create(nombre=datos["ciudad"])

    condominio = Condominio.objects.create(
        nombre=datos["nombre"],
        direccion=datos["direccion"],
        ciudad=ciudad,
        edificios=datos["edificios"],  # Condominio.save() crea los Edificio vacíos
        portada=datos.get("portada") or "",
        estimado_personas_departamento=datos.get("estimado_personas_departamento"),
    )
    # A cada edificio se le asignan pisos y viviendas por piso; al guardarlo,
    # Edificio.save() genera sus unidades (1-101, 1-102, ...).
    for edificio in condominio.edificios_set.all():
        edificio.numero_pisos = datos["numero_pisos"]
        edificio.viviendas_por_piso = datos["viviendas_por_piso"]
        edificio.save()
    return condominio

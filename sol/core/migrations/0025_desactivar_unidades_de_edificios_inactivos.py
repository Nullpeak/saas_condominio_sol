"""
Deja coherentes los datos existentes: los edificios que ya estaban desactivados tenían
sus unidades activas. Se desactivan, marcadas como "desactivada_por_edificio" para que
vuelvan al reactivar el edificio.
"""
from django.db import migrations


def desactivar(apps, schema_editor):
    Unidad = apps.get_model("core", "Unidad")
    Unidad.objects.filter(edificio__activo=False, activo=True).update(activo=False, desactivada_por_edificio=True)


def revertir(apps, schema_editor):
    Unidad = apps.get_model("core", "Unidad")
    Unidad.objects.filter(desactivada_por_edificio=True).update(activo=True, desactivada_por_edificio=False)


class Migration(migrations.Migration):
    dependencies = [("core", "0024_unidad_desactivada_por_edificio")]
    operations = [migrations.RunPython(desactivar, revertir)]

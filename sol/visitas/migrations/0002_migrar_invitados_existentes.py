from django.db import migrations


def copiar_invitados(apps, schema_editor):
    Invitado = apps.get_model("core", "Invitado")
    Visita = apps.get_model("visitas", "Visita")
    alias = schema_editor.connection.alias

    estados = {
        "pendiente": "pendiente",
        "presente": "presente",
        "sin_registro": "sin_registro",
        "check_in": "presente",
        "check_out": "finalizada",
    }
    for invitado in Invitado.objects.using(alias).all().iterator():
        Visita.objects.using(alias).create(
            condominio_id=invitado.condominio_id,
            edificio_id=invitado.edificio_id,
            unidad_id=invitado.unidad_id,
            registrado_por_id=invitado.registrado_por_id,
            nombre=invitado.nombre,
            fecha_visita=invitado.fecha_visita,
            motivo=invitado.motivo,
            patente_vehiculo=invitado.patente_vehiculo,
            tiempo_llegada=invitado.tiempo_llegada,
            tiempo_salida=invitado.tiempo_salida,
            estado=estados.get(invitado.estado, "pendiente"),
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_visitas_programadas"),
        ("visitas", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copiar_invitados, migrations.RunPython.noop),
    ]

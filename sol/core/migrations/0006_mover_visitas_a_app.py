import django.db.models.deletion
from django.db import migrations, models


def normalizar_invitados_reservas(apps, schema_editor):
    Invitado = apps.get_model("core", "Invitado")
    alias = schema_editor.connection.alias
    Invitado.objects.using(alias).filter(estado="presente").update(estado="check_in")
    Invitado.objects.using(alias).filter(estado="sin_registro").update(estado="pendiente")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_visitas_programadas"),
        ("visitas", "0002_migrar_invitados_existentes"),
    ]

    operations = [
        migrations.RemoveField(model_name="invitado", name="fecha_visita"),
        migrations.RemoveField(model_name="invitado", name="motivo"),
        migrations.RemoveField(model_name="invitado", name="registrado_por"),
        migrations.RunPython(normalizar_invitados_reservas, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="invitado",
            name="estado",
            field=models.CharField(choices=[("pendiente", "Pendiente"), ("check_in", "Check-in"), ("check_out", "Check-out")], default="pendiente", max_length=10),
        ),
    ]

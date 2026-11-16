import django.utils.timezone
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_remove_pago_cantidad_residentes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="invitado",
            name="fecha_visita",
            field=models.DateField(db_index=True, default=django.utils.timezone.localdate),
        ),
        migrations.AddField(
            model_name="invitado",
            name="motivo",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="invitado",
            name="registrado_por",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitas_registradas", to="core.usuario"),
        ),
        migrations.AlterField(
            model_name="invitado",
            name="estado",
            field=models.CharField(choices=[("pendiente", "Pendiente"), ("presente", "Presente"), ("sin_registro", "Sin registro"), ("check_in", "Check-in"), ("check_out", "Check-out")], default="pendiente", max_length=15),
        ),
    ]

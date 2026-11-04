import django.utils.timezone
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0005_visitas_programadas"),
    ]

    operations = [
        migrations.CreateModel(
            name="Visita",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=100)),
                ("fecha_visita", models.DateField(db_index=True, default=django.utils.timezone.localdate)),
                ("motivo", models.CharField(blank=True, max_length=160)),
                ("patente_vehiculo", models.CharField(blank=True, max_length=10)),
                ("tiempo_llegada", models.DateTimeField(blank=True, null=True)),
                ("tiempo_salida", models.DateTimeField(blank=True, null=True)),
                ("estado", models.CharField(choices=[("pendiente", "Programada"), ("presente", "Presente"), ("sin_registro", "Sin registro"), ("finalizada", "Salida registrada")], db_index=True, default="pendiente", max_length=15)),
                ("condominio", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitas", to="core.condominio")),
                ("edificio", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitas", to="core.edificio")),
                ("registrado_por", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitas_registradas", to="core.usuario")),
                ("unidad", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="visitas", to="core.unidad")),
            ],
            options={
                "db_table": "visita",
                "ordering": ["fecha_visita", "nombre"],
            },
        ),
        migrations.AddIndex(
            model_name="visita",
            index=models.Index(fields=["condominio", "fecha_visita"], name="visita_condominio_fecha_idx"),
        ),
    ]

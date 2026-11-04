import django.db.models.deletion
import tickets.models
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("core", "0006_mover_visitas_a_app"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name="Ticket",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("titulo", models.CharField(max_length=150)),
                        ("descripcion", models.TextField()),
                        ("estado", models.CharField(choices=[("abierto", "Abierto"), ("en_revision", "En revisión"), ("resuelto", "Resuelto"), ("cerrado", "Cerrado")], default="abierto", max_length=15)),
                        ("prioridad", models.CharField(blank=True, choices=[("baja", "Baja"), ("media", "Media"), ("alta", "Alta")], max_length=5)),
                        ("creado", models.DateTimeField(auto_now_add=True)),
                        ("resuelto", models.DateTimeField(blank=True, null=True)),
                        ("asignado", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets_asignados", to="core.usuario")),
                        ("condominio", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tickets", to="core.condominio")),
                        ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tickets_creados", to="core.usuario")),
                    ],
                    options={
                        "db_table": "ticket",
                        "ordering": ["-creado", "-id"],
                    },
                ),
                migrations.CreateModel(
                    name="TicketArchivo",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("archivo", models.FileField(upload_to=tickets.models.ticket_archivo_path)),
                        ("subido", models.DateTimeField(auto_now_add=True)),
                        ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pruebas", to="tickets.ticket")),
                    ],
                    options={
                        "db_table": "ticket_archivo",
                        "ordering": ["subido", "id"],
                    },
                ),
                migrations.AddIndex(
                    model_name="ticket",
                    index=models.Index(fields=["estado"], name="ticket_estado_880f6e_idx"),
                ),
            ],
        ),
    ]

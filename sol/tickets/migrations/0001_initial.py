# Traspaso de Ticket y TicketArchivo desde la app core.
# Las tablas "ticket" y "ticket_archivo" ya existen en la base de datos (las creó core.0001),
# así que aquí solo se registran en el estado de Django: no se ejecuta SQL.
# core.0005 hace lo opuesto (las saca del estado de core, también sin SQL).

import django.db.models.deletion
from django.db import migrations, models

import tickets.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("core", "0004_remove_pago_cantidad_residentes_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
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
                        ("condominio", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tickets", to="core.condominio")),
                        ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tickets_creados", to="core.usuario")),
                        ("asignado", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets_asignados", to="core.usuario")),
                    ],
                    options={"db_table": "ticket"},
                ),
                migrations.AddIndex(
                    model_name="ticket",
                    index=models.Index(fields=["estado"], name="ticket_estado_880f6e_idx"),
                ),
                migrations.CreateModel(
                    name="TicketArchivo",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("archivo", models.FileField(upload_to=tickets.models.ticket_archivo_path)),
                        ("subido", models.DateTimeField(auto_now_add=True)),
                        ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="pruebas", to="tickets.ticket")),
                    ],
                    options={"db_table": "ticket_archivo"},
                ),
            ],
            database_operations=[],
        ),
    ]

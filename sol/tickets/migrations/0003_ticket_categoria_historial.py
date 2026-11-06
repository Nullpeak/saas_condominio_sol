import django.db.models.deletion
from django.db import migrations, models


def guardar_estado_actual(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    Historial = apps.get_model("tickets", "TicketHistorial")
    alias = schema_editor.connection.alias
    Historial.objects.using(alias).bulk_create([
        Historial(ticket_id=ticket.pk, tipo="creado", detalle="Ticket existente al iniciar el historial.", estado_nuevo=ticket.estado)
        for ticket in Ticket.objects.using(alias).only("pk", "estado").iterator()
    ])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_alter_reservacion_estado"),
        ("tickets", "0002_historial_y_adjunto"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="categoria",
            field=models.CharField(
                choices=[
                    ("agua", "Agua o filtración"),
                    ("electricidad", "Electricidad"),
                    ("infraestructura", "Infraestructura"),
                    ("ruido", "Ruido o convivencia"),
                    ("otro", "Otro"),
                ],
                default="otro",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="estado",
            field=models.CharField(
                choices=[
                    ("abierto", "Pendiente"),
                    ("en_revision", "En revisión"),
                    ("resuelto", "Resuelto"),
                    ("cerrado", "Cerrado"),
                    ("cancelado", "Cancelado"),
                ],
                default="abierto",
                max_length=15,
            ),
        ),
        migrations.CreateModel(
            name="TicketHistorial",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("creado", "Ticket creado"), ("edicion", "Ticket actualizado"), ("estado", "Cambio de estado"), ("asignacion", "Cambio de asignación"), ("respuesta", "Respuesta"), ("cancelacion", "Ticket cancelado")], max_length=15)),
                ("detalle", models.TextField(blank=True)),
                ("estado_anterior", models.CharField(blank=True, max_length=15)),
                ("estado_nuevo", models.CharField(blank=True, max_length=15)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("ticket", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="historial", to="tickets.ticket")),
                ("usuario", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="historial_tickets", to="core.usuario")),
            ],
            options={"db_table": "ticket_historial", "ordering": ["creado", "id"]},
        ),
        migrations.RunPython(guardar_estado_actual, migrations.RunPython.noop),
    ]

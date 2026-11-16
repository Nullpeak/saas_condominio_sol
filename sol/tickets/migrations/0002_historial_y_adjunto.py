import django.db.models.deletion
import tickets.models
import tickets.validators
from django.db import migrations, models


def vincular_unidades_existentes(apps, schema_editor):
    Ticket = apps.get_model("tickets", "Ticket")
    Unidad = apps.get_model("core", "Unidad")
    alias = schema_editor.connection.alias
    tickets_sin_unidad = Ticket.objects.using(alias).filter(unidad__isnull=True).select_related("usuario")
    for ticket in tickets_sin_unidad.iterator():
        unidades = Unidad.objects.using(alias).filter(
            edificio__condominio_id=ticket.condominio_id,
        ).filter(
            models.Q(dueno_id=ticket.usuario_id) | models.Q(arrendatario_id=ticket.usuario_id)
        )
        if unidades.count() == 1:
            ticket.unidad_id = unidades.values_list("id", flat=True).first()
            ticket.save(update_fields=["unidad"])


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_mover_visitas_a_app"),
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="ticket",
            name="unidad",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets", to="core.unidad"),
        ),
        migrations.AddField(
            model_name="ticket",
            name="resuelto_por",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tickets_resueltos", to="core.usuario"),
        ),
        migrations.AlterField(
            model_name="ticket",
            name="estado",
            field=models.CharField(choices=[("abierto", "Pendiente"), ("en_revision", "En revisión"), ("resuelto", "Resuelto"), ("cerrado", "Cerrado")], default="abierto", max_length=15),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="ticketarchivo",
                    name="ticket",
                    field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="archivos", to="tickets.ticket"),
                ),
                migrations.AlterField(
                    model_name="ticketarchivo",
                    name="archivo",
                    field=models.FileField(upload_to=tickets.models.ticket_archivo_path, validators=[tickets.validators.validar_adjunto]),
                ),
            ],
        ),
        migrations.RunPython(vincular_unidades_existentes, migrations.RunPython.noop),
    ]

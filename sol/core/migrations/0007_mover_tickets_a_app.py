from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_mover_visitas_a_app"),
        ("tickets", "0002_historial_y_adjunto"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(name="TicketArchivo"),
                migrations.DeleteModel(name="Ticket"),
            ],
        ),
    ]

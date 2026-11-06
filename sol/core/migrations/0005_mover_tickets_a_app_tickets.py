# Ticket y TicketArchivo ahora viven en la app "tickets" (ver tickets/migrations/0001).
# Solo se quitan del estado de core: las tablas siguen en la base de datos y las usa la nueva app.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_remove_pago_cantidad_residentes_and_more"),
        ("tickets", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name="TicketArchivo"),
                migrations.DeleteModel(name="Ticket"),
            ],
            database_operations=[],
        ),
    ]

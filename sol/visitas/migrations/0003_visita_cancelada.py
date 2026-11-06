from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("visitas", "0002_migrar_invitados_existentes")]

    operations = [
        migrations.AlterField(
            model_name="visita",
            name="estado",
            field=models.CharField(
                choices=[
                    ("pendiente", "Programada"),
                    ("presente", "Presente"),
                    ("sin_registro", "Sin registro"),
                    ("finalizada", "Salida registrada"),
                    ("cancelada", "Cancelada"),
                ],
                db_index=True,
                default="pendiente",
                max_length=15,
            ),
        ),
    ]

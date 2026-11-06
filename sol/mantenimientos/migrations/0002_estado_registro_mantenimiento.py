from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mantenimientos", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="registromantenimiento",
            name="estado",
            field=models.CharField(
                choices=[("programado", "Programado"), ("en_curso", "En curso"), ("completado", "Completado")],
                default="completado",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="registromantenimiento",
            name="detalle_cierre",
            field=models.TextField(blank=True),
        ),
    ]

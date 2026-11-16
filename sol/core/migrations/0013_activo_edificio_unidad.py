from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0012_alter_reservacion_estado")]

    operations = [
        migrations.AddField(
            model_name="edificio",
            name="activo",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="unidad",
            name="activo",
            field=models.BooleanField(default=True),
        ),
    ]

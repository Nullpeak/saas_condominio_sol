from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_activo_edificio_unidad"),
    ]

    operations = [
        migrations.AddField(
            model_name="areacomun",
            name="requiere_gastos_al_dia",
            field=models.BooleanField(default=False),
        ),
    ]

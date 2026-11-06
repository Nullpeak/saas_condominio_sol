# Generated for the Mercado Pago test checkout integration.
import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("gastos_comunes", "0002_alter_periodo_anio"),
        ("core", "0011_unidad_alicuota"),
    ]

    operations = [
        migrations.CreateModel(
            name="PagoOnline",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("referencia_externa", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("clave_idempotencia", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("orden_proveedor", models.CharField(blank=True, max_length=80, null=True, unique=True)),
                ("url_pago", models.URLField(blank=True, max_length=1000)),
                ("monto", models.DecimalField(decimal_places=0, max_digits=14, validators=[django.core.validators.MinValueValidator(1)])),
                ("estado", models.CharField(choices=[("creado", "Creado"), ("pendiente", "Pendiente"), ("aprobado", "Aprobado"), ("rechazado", "Rechazado"), ("cancelado", "Cancelado"), ("error", "Error de creación")], default="creado", max_length=12)),
                ("detalle_estado", models.CharField(blank=True, max_length=100)),
                ("creado", models.DateTimeField(auto_now_add=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("pagado", models.DateTimeField(blank=True, null=True)),
                ("liquidacion", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pagos_online", to="gastos_comunes.liquidacion")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="pagos_online", to="core.usuario")),
            ],
            options={"ordering": ["-creado", "-id"]},
        ),
        migrations.AddField(
            model_name="pagoregistrado",
            name="pago_online",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pago_registrado", to="gastos_comunes.pagoonline"),
        ),
    ]

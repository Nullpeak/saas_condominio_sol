from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_alter_historialadministrativo_accion"),
        ("gastos_comunes", "0006_mercadopagoconexion_modo_prueba_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="pagoonline",
            name="proveedor",
            field=models.CharField(
                choices=[("mercadopago", "Mercado Pago"), ("webpay", "Webpay Plus")],
                default="mercadopago",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="pagoonline",
            name="token_webpay",
            field=models.CharField(blank=True, max_length=80, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="pagoonline",
            name="codigo_autorizacion",
            field=models.CharField(blank=True, max_length=20),
        ),
        migrations.AlterField(
            model_name="pagoonline",
            name="proveedor",
            field=models.CharField(
                choices=[("mercadopago", "Mercado Pago"), ("webpay", "Webpay Plus")],
                default="webpay",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="WebpayConexion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("codigo_comercio", models.CharField(blank=True, max_length=20)),
                ("api_key_cifrada", models.TextField(blank=True)),
                ("modo_prueba", models.BooleanField(default=True)),
                ("actualizado", models.DateTimeField(auto_now=True)),
                ("condominio", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="webpay_conexion", to="core.condominio")),
                ("configurado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="conexiones_webpay", to="core.usuario")),
            ],
        ),
    ]

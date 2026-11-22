from django.urls import path
from . import views

app_name = "gastos_comunes"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.periodo_nuevo, name="periodo_nuevo"),
    path("periodo/<int:periodo_id>/eliminar/", views.periodo_eliminar, name="periodo_eliminar"),
    path("periodo/<int:periodo_id>/", views.detalle, name="detalle"),
    path("periodo/<int:periodo_id>/buscar-unidades-pago/", views.buscar_unidades_pago, name="buscar_unidades_pago"),
    path("unidad/<int:unidad_id>/cartola/", views.cartola_unidad, name="cartola_unidad"),
    path("pagos/iniciar/<int:liquidacion_id>/", views.iniciar_pago_online, name="pago_iniciar"),
    path("pagos/resultado/<uuid:pago_id>/", views.resultado_pago_online, name="pago_resultado"),
    path("pagos/webpay/retorno/<uuid:pago_id>/", views.webpay_retorno, name="webpay_retorno"),
    path("pagos/mercadopago/notificacion/", views.mercadopago_webhook, name="mercadopago_webhook"),
    path("mercadopago/cuentas/", views.mercadopago_cuentas, name="mercadopago_cuentas"),
    path("mercadopago/cuentas/<int:condominio_id>/conectar/", views.mercadopago_conectar, name="mercadopago_conectar"),
    path("mercadopago/callback/", views.mercadopago_callback, name="mercadopago_callback"),
    path("mercadopago/cuentas/<int:condominio_id>/desconectar/", views.mercadopago_desconectar, name="mercadopago_desconectar"),
    path("webpay/cuentas/", views.webpay_cuentas, name="webpay_cuentas"),
    path("webpay/cuentas/<int:condominio_id>/configurar/", views.webpay_configurar, name="webpay_configurar"),
    path("periodo/<int:periodo_id>/plantilla/<str:tipo>.xlsx", views.descargar_plantilla, name="plantilla"),
    path("periodo/<int:periodo_id>/lecturas.xlsx", views.descargar_lecturas_cargadas, name="descargar_lecturas"),
]

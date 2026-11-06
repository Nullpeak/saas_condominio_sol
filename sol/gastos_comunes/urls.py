from django.urls import path
from . import views

app_name = "gastos_comunes"
urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.periodo_nuevo, name="periodo_nuevo"),
    path("periodo/<int:periodo_id>/eliminar/", views.periodo_eliminar, name="periodo_eliminar"),
    path("periodo/<int:periodo_id>/", views.detalle, name="detalle"),
    path("pagos/iniciar/<int:liquidacion_id>/", views.iniciar_pago_online, name="pago_iniciar"),
    path("pagos/resultado/<uuid:pago_id>/", views.resultado_pago_online, name="pago_resultado"),
    path("pagos/mercadopago/notificacion/", views.mercadopago_webhook, name="mercadopago_webhook"),
    path("periodo/<int:periodo_id>/plantilla/<str:tipo>.xlsx", views.descargar_plantilla, name="plantilla"),
    path("periodo/<int:periodo_id>/lecturas.xlsx", views.descargar_lecturas_cargadas, name="descargar_lecturas"),
]

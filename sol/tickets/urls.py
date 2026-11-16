from django.urls import path

from . import views

app_name = "tickets"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.crear, name="crear"),
    path("<int:ticket_id>/", views.detalle, name="detalle"),
    path("<int:ticket_id>/editar/", views.editar, name="editar"),
    path("<int:ticket_id>/comentar/", views.comentar, name="comentar"),
    path("<int:ticket_id>/gestionar/", views.gestionar, name="gestionar"),
    path("<int:ticket_id>/<str:nombre>/", views.accion, name="accion"),
    path("archivo/<int:archivo_id>/", views.archivo, name="archivo"),
]

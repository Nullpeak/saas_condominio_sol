from django.urls import path

from . import views

app_name = "mantenimientos"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.crear_elemento, name="crear_elemento"),
    path("equipo/<int:elemento_id>/", views.detalle, name="detalle"),
    path("equipo/<int:elemento_id>/editar/", views.editar_elemento, name="editar_elemento"),
    path("registro/<int:registro_id>/editar/", views.editar_registro, name="editar_registro"),
    path("documento/<int:documento_id>/", views.descargar_documento, name="documento"),
]

from django.urls import path
from . import views


app_name = "visitas"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nueva/", views.crear, name="crear"),
    path("<int:visita_id>/editar/", views.editar, name="editar"),
    path("<int:visita_id>/cancelar/", views.cancelar, name="cancelar"),
    path("<int:visita_id>/presente/", views.marcar_presente, name="marcar_presente"),
]

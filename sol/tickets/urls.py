from django.urls import path
from . import views


app_name = "tickets"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.crear, name="crear"),
    path("<int:ticket_id>/asignar/", views.asignar, name="asignar"),
    path("<int:ticket_id>/estado/", views.cambiar_estado, name="cambiar_estado"),
    path("archivos/<int:archivo_id>/", views.archivo, name="archivo"),
]

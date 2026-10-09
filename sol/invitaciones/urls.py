from django.urls import path

from . import views

app_name = "invitaciones"

urlpatterns = [
    path("invitar/", views.invitar, name="invitar"),
    path("invitacion/<str:token>/", views.aceptar, name="aceptar"),
]
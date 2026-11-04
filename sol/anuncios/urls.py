from django.urls import path

from . import views

app_name = "anuncios"

urlpatterns = [
    path("", views.lista, name="lista"),
    path("nuevo/", views.crear, name="crear"),
    path("imagen/<str:tipo>/<int:anuncio_id>/", views.imagen, name="imagen"),
    path("<int:anuncio_id>/<str:accion>/", views.moderar, name="moderar"),
]

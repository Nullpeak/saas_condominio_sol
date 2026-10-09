from django.urls import path

from . import views

app_name = "launch"

urlpatterns = [
    path("", views.launch, name="launch"),
    path("ciudades/", views.ciudades, name="ciudades"),
]
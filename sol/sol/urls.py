"""
URL configuration for sol project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include

from core.views import *


urlpatterns = [

    #============== DJANGO ADMIN ==============

    path(
        "admin/",
        admin.site.urls
    ),


    #============== AUTENTICACIÓN ==============

    path(
        "",
        landing,
        name="landing"
    ),

    path(
        "login/",
        login_view,
        name="login"
    ),

    path(
        "logout/",
        logout_view,
        name="logout"
    ),

    path(
        "recuperar-contrasena/",
        password_reset_request,
        name="password_reset_request"
    ),

    path(
        "recuperar-contrasena/enviado/",
        password_reset_done,
        name="password_reset_done"
    ),

    path(
        "recuperar-contrasena/<uidb64>/<token>/",
        password_reset_confirm,
        name="password_reset_confirm"
    ),

    path(
        "recuperar-contrasena/completado/",
        password_reset_complete,
        name="password_reset_complete"
    ),


    #============== DASHBOARD ==============

    path(
        "inicio/",
        inicio,
        name="inicio"
    ),


    #============== ADMINISTRACIÓN ==============

    path(
        "administracion/",
        administracion,
        name="administracion"
    ),


    #============== USUARIOS ==============

    path(
        "usuarios/",
        usuarios_lista,
        name="usuarios_lista"
    ),

    path(
        "usuarios/nuevo/",
        usuario_crear,
        name="usuario_crear"
    ),

    path(
        "usuarios/<int:usuario_id>/editar/",
        usuario_editar,
        name="usuario_editar"
    ),

    path(
        "usuarios/<int:usuario_id>/cambiar-estado/",
        usuario_cambiar_estado,
        name="usuario_cambiar_estado"
    ),


    #============== CONDOMINIOS ==============

    path(
        "condominios/",
        condominios_lista,
        name="condominios_lista"
    ),

    path(
        "condominios/nuevo/",
        condominio_crear,
        name="condominio_crear"
    ),

    path(
        "condominios/<int:condominio_id>/editar/",
        condominio_editar,
        name="condominio_editar"
    ),


    #============== EDIFICIOS ==============

    path(
        "condominios/<int:condominio_id>/edificios/",
        edificios_lista,
        name="edificios_lista"
    ),

    path(
        "edificios/<int:edificio_id>/editar/",
        edificio_editar,
        name="edificio_editar"
    ),


    #============== UNIDADES ==============

    path(
        "edificios/<int:edificio_id>/unidades/",
        unidades_lista,
        name="unidades_lista"
    ),

    path(
        "unidades/<int:unidad_id>/editar/",
        unidad_editar,
        name="unidad_editar"
    ),


    #============== ÁREAS COMUNES ==============

    path(
        "condominios/<int:condominio_id>/areas-comunes/",
        areas_comunes_lista,
        name="areas_comunes_lista"
    ),

    path(
        "condominios/<int:condominio_id>/areas-comunes/nueva/",
        area_comun_crear,
        name="area_comun_crear"
    ),

    path(
        "areas-comunes/<int:area_id>/editar/",
        area_comun_editar,
        name="area_comun_editar"
    ),


    #============== HORARIOS ÁREAS COMUNES ==============

    path(
        "areas-comunes/<int:area_id>/horarios/",
        area_comun_horarios,
        name="area_comun_horarios"
    ),

    path(
        "horarios-area-comun/<int:horario_id>/actualizar/",
        area_comun_horario_actualizar,
        name="area_comun_horario_actualizar"
    ),

    path(
        "horarios-area-comun/<int:horario_id>/eliminar/",
        area_comun_horario_eliminar,
        name="area_comun_horario_eliminar"
    ),


    #============== RESERVACIONES ==============

    path(
        "reservaciones/",
        reservaciones_lista,
        name="reservaciones_lista"
    ),

    path(
        "reservaciones/nueva/",
        reservacion_crear,
        name="reservacion_crear"
    ),

    # NUEVO:
    # Devuelve los horarios configurados y las reservas
    # existentes para construir la agenda visual.
    path(
        "reservaciones/disponibilidad/",
        reservacion_disponibilidad,
        name="reservacion_disponibilidad"
    ),

    path(
        "reservaciones/<int:reservacion_id>/editar/",
        reservacion_editar,
        name="reservacion_editar"
    ),

    path(
        "reservaciones/<int:reservacion_id>/cancelar/",
        reservacion_cancelar,
        name="reservacion_cancelar"
    ),
    path("launch/", include("launch.urls")),
    path("", include("invitaciones.urls"))
]
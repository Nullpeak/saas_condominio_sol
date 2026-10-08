import json
from datetime import time

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import (
    PasswordResetView,
    PasswordResetDoneView,
    PasswordResetConfirmView,
    PasswordResetCompleteView,
)
from django.urls import reverse_lazy
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse

from .validators import *
from .models import (
    Usuario,
    Rol,
    Condominio,
    Ciudad,
    Edificio,
    Unidad,
    AreaComun,
    HorarioAreaComun,
)


# ============================== FUNCIONES AUXILIARES ==============================

def obtener_usuario(request):
    return request.user.usuario


def tiene_rol(usuario, *roles):
    return usuario.rol.nombre in roles


def es_super_administrador(usuario):
    return tiene_rol(usuario, "Super Administrador")


def es_administrador(usuario):
    return tiene_rol(usuario, "Administrador")


def es_gestor(usuario):
    return tiene_rol(usuario, "Gestor")


def es_residente(usuario):
    return tiene_rol(usuario, "Residente")


def puede_gestionar_usuarios(usuario):
    return tiene_rol(usuario, "Super Administrador", "Administrador")


def puede_gestionar_condominio(usuario):
    return tiene_rol(usuario, "Super Administrador", "Administrador")


def puede_gestionar_roles(usuario):
    return es_super_administrador(usuario)


# ============================== AUTENTICACIÓN ==============================

def login_view(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        usuario_django = authenticate(
            request,
            username=username,
            password=password
        )

        if usuario_django is not None:
            try:
                usuario = Usuario.objects.select_related(
                    "rol",
                    "condominio"
                ).get(user=usuario_django)

            except Usuario.DoesNotExist:
                return render(request, "core/login.html", {
                    "error": "La cuenta no está configurada correctamente."
                })

            if not usuario.activo:
                return render(request, "core/login.html", {
                    "error": "Esta cuenta se encuentra desactivada."
                })

            login(request, usuario_django)
            return redirect("inicio")

        return render(request, "core/login.html", {
            "error": "Usuario o contraseña incorrectos."
        })

    return render(request, "core/login.html")


@login_required
def logout_view(request):
    logout(request)
    return redirect("landing")


# ============================== HU-010: RECUPERAR CONTRASEÑA ==============================

def password_reset_request(request):
    for usuario in Usuario.objects.select_related("user").filter(
        user__isnull=False
    ):
        if usuario.user.email != usuario.email:
            usuario.user.email = usuario.email
            usuario.user.save(update_fields=["email"])

    vista = PasswordResetView.as_view(
        template_name="core/password_reset.html",
        email_template_name="core/password_reset_email.html",
        subject_template_name="core/password_reset_subject.txt",
        success_url=reverse_lazy("password_reset_done")
    )

    return vista(request)


def password_reset_done(request):
    vista = PasswordResetDoneView.as_view(
        template_name="core/password_reset_done.html"
    )

    return vista(request)


def password_reset_confirm(request, uidb64, token):
    vista = PasswordResetConfirmView.as_view(
        template_name="core/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete")
    )

    return vista(
        request,
        uidb64=uidb64,
        token=token
    )


def password_reset_complete(request):
    vista = PasswordResetCompleteView.as_view(
        template_name="core/password_reset_complete.html"
    )

    return vista(request)


# ============================== DASHBOARD ==============================

@login_required
def inicio(request):
    usuario = obtener_usuario(request)

    return render(request, "core/inicio.html", {
        "usuario": usuario,
    })


# ============================== ADMINISTRACIÓN ==============================

@login_required
def administracion(request):
    usuario = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario):
        return redirect("inicio")

    if es_super_administrador(usuario):
        condominios = Condominio.objects.filter(activo=True)

        usuarios = Usuario.objects.select_related(
            "user",
            "rol",
            "condominio"
        ).all()

    else:
        condominios = Condominio.objects.filter(
            id=usuario.condominio_id,
            activo=True
        )

        usuarios = Usuario.objects.select_related(
            "user",
            "rol",
            "condominio"
        ).filter(
            condominio=usuario.condominio
        )

    return render(request, "core/administracion.html", {
        "usuario": usuario,
        "condominios": condominios,
        "usuarios": usuarios,
    })


# ============================== LISTA DE USUARIOS ==============================

@login_required
def usuarios_lista(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    if es_super_administrador(usuario_actual):
        usuarios = Usuario.objects.select_related(
            "user",
            "rol",
            "condominio"
        ).all()

    else:
        usuarios = Usuario.objects.select_related(
            "user",
            "rol",
            "condominio"
        ).filter(
            condominio=usuario_actual.condominio
        )

    return render(request, "core/usuarios/lista.html", {
        "usuario": usuario_actual,
        "usuarios": usuarios,
    })


# ============================== CREAR USUARIO ==============================

@login_required
def usuario_crear(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    roles = Rol.objects.all()

    if es_super_administrador(usuario_actual):
        condominios = Condominio.objects.filter(activo=True)

    else:
        condominios = Condominio.objects.filter(
            id=usuario_actual.condominio_id,
            activo=True
        )

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        password_confirmacion = request.POST.get(
            "password_confirmacion",
            ""
        )
        nombre = request.POST.get("nombre", "").strip()
        nombre2 = request.POST.get("nombre2", "").strip()
        apellido = request.POST.get("apellido", "").strip()
        apellido2 = request.POST.get("apellido2", "").strip()
        email = request.POST.get("email", "").strip().lower()
        telefono = request.POST.get("telefono", "").strip()
        rut = request.POST.get("rut", "").strip()
        rol_id = request.POST.get("rol", "").strip()
        condominio_id = request.POST.get("condominio", "").strip()

        errores = []

        # ============================== DATOS BÁSICOS ==============================

        if not username:
            errores.append(
                "Debes ingresar un nombre de usuario."
            )

        if not password:
            errores.append(
                "Debes ingresar una contraseña."
            )

        if password != password_confirmacion:
            errores.append(
                "Las contraseñas no coinciden."
            )

        if not nombre:
            errores.append(
                "Debes ingresar el nombre."
            )

        if not apellido:
            errores.append(
                "Debes ingresar el apellido."
            )

        if not email:
            errores.append(
                "Debes ingresar un correo electrónico."
            )

        if not rol_id:
            errores.append(
                "Debes seleccionar un rol."
            )

        # ============================== VALIDAR RUT ==============================

        rut_normalizado = None

        if not rut:
            errores.append(
                "Debes ingresar el RUT."
            )

        else:
            try:
                validar_rut(rut)

                rut_normalizado = formatear_rut(rut)

                if Usuario.objects.filter(
                    rut=rut_normalizado
                ).exists():
                    errores.append(
                        "El RUT ya está registrado."
                    )

            except ValidationError as error:
                errores.extend(error.messages)

        # ============================== VALIDAR TELÉFONO ==============================

        telefono_normalizado = None

        if telefono:
            try:
                validar_telefono(telefono)
                telefono_normalizado = formatear_telefono(telefono)

            except ValidationError as error:
                errores.extend(error.messages)

        # ============================== VALIDAR USUARIO ==============================

        if username and User.objects.filter(
            username=username
        ).exists():
            errores.append(
                "El nombre de usuario ya está registrado."
            )

        # ============================== VALIDAR CORREO ==============================

        if email:
            try:
                validar_email(email)

            except ValidationError as error:
                errores.extend(error.messages)

            if Usuario.objects.filter(
                email=email
            ).exists():
                errores.append(
                    "El correo electrónico ya está registrado."
                )

        # ============================== OBTENER ROL ==============================

        rol = None

        if rol_id:
            try:
                rol = Rol.objects.get(
                    id=int(rol_id)
                )

            except (Rol.DoesNotExist, ValueError):
                errores.append(
                    "El rol seleccionado no es válido."
                )

        # ============================== OBTENER CONDOMINIO ==============================

        condominio = None

        if condominio_id:
            try:
                condominio = Condominio.objects.get(
                    id=int(condominio_id)
                )

            except (Condominio.DoesNotExist, ValueError):
                errores.append(
                    "El condominio seleccionado no es válido."
                )

        # ============================== PERMISOS DEL ADMINISTRADOR ==============================

        if es_administrador(usuario_actual):

            if rol and rol.nombre == "Super Administrador":
                errores.append(
                    "Un Administrador no puede crear un Super Administrador."
                )

            if condominio_id:
                try:
                    condominio_id_numero = int(condominio_id)

                    if (
                        not usuario_actual.condominio_id
                        or condominio_id_numero != usuario_actual.condominio_id
                    ):
                        errores.append(
                            "Solo puedes crear usuarios dentro de tu propio condominio."
                        )

                except ValueError:
                    errores.append(
                        "El condominio seleccionado no es válido."
                    )

            condominio = usuario_actual.condominio

        # ============================== CREAR USUARIO ==============================

        if not errores:

            with transaction.atomic():

                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=email
                )

                Usuario.objects.create(
                    user=user,
                    condominio=condominio,
                    nombre=nombre,
                    nombre2=nombre2,
                    apellido=apellido,
                    apellido2=apellido2,
                    rol=rol,
                    email=email,
                    telefono=telefono_normalizado,
                    rut=rut_normalizado,
                    activo=True,
                )

            return redirect("usuarios_lista")

        return render(request, "core/usuarios/crear.html", {
            "usuario": usuario_actual,
            "roles": roles,
            "condominios": condominios,
            "errores": errores,
            "datos": request.POST,
        })

    return render(request, "core/usuarios/crear.html", {
        "usuario": usuario_actual,
        "roles": roles,
        "condominios": condominios,
    })


# ============================== EDITAR USUARIO ==============================

@login_required
def usuario_editar(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related(
            "user",
            "rol",
            "condominio"
        ),
        id=usuario_id
    )

    if es_administrador(usuario_actual):

        if usuario.condominio_id != usuario_actual.condominio_id:
            return redirect("usuarios_lista")

        if es_super_administrador(usuario):
            return redirect("usuarios_lista")

    roles = Rol.objects.all()

    if es_super_administrador(usuario_actual):
        condominios = Condominio.objects.filter(activo=True)

    else:
        condominios = Condominio.objects.filter(
            id=usuario_actual.condominio_id,
            activo=True
        )

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        nombre2 = request.POST.get("nombre2", "").strip()
        apellido = request.POST.get("apellido", "").strip()
        apellido2 = request.POST.get("apellido2", "").strip()
        email = request.POST.get("email", "").strip().lower()
        telefono = request.POST.get("telefono", "").strip()
        rut = request.POST.get("rut", "").strip()
        rol_id = request.POST.get("rol", "").strip()
        condominio_id = request.POST.get("condominio", "").strip()
        activo = request.POST.get("activo") == "on"

        errores = []

        # ============================== DATOS BÁSICOS ==============================

        if not nombre:
            errores.append(
                "Debes ingresar el nombre."
            )

        if not apellido:
            errores.append(
                "Debes ingresar el apellido."
            )

        if not email:
            errores.append(
                "Debes ingresar un correo."
            )

        # ============================== VALIDAR RUT ==============================

        rut_normalizado = None

        if not rut:
            errores.append(
                "Debes ingresar el RUT."
            )

        else:
            try:
                validar_rut(rut)

                rut_normalizado = formatear_rut(rut)

                if Usuario.objects.filter(
                    rut=rut_normalizado
                ).exclude(
                    id=usuario.id
                ).exists():
                    errores.append(
                        "El RUT ya está registrado."
                    )

            except ValidationError as error:
                errores.extend(error.messages)

        # ============================== VALIDAR TELÉFONO ==============================

        telefono_normalizado = None

        if telefono:
            try:
                validar_telefono(telefono)
                telefono_normalizado = formatear_telefono(telefono)

            except ValidationError as error:
                errores.extend(error.messages)

        # ============================== VALIDAR CORREO ==============================

        if email:

            try:
                validar_email(email)

            except ValidationError as error:
                errores.extend(error.messages)

            if Usuario.objects.filter(
                email=email
            ).exclude(
                id=usuario.id
            ).exists():
                errores.append(
                    "El correo electrónico ya está registrado."
                )

        # ============================== OBTENER ROL ==============================

        rol = None

        if not rol_id:
            errores.append(
                "Debes seleccionar un rol."
            )

        else:
            try:
                rol = Rol.objects.get(
                    id=int(rol_id)
                )

            except (Rol.DoesNotExist, ValueError):
                errores.append(
                    "El rol seleccionado no es válido."
                )

        # ============================== OBTENER CONDOMINIO ==============================

        condominio = None

        if es_administrador(usuario_actual):

            condominio = usuario_actual.condominio

            if not condominio:
                errores.append(
                    "El Administrador no tiene un condominio asociado."
                )

        else:

            if condominio_id:
                try:
                    condominio = Condominio.objects.get(
                        id=int(condominio_id)
                    )

                except (Condominio.DoesNotExist, ValueError):
                    errores.append(
                        "El condominio seleccionado no es válido."
                    )

            else:
                condominio = None

        # ============================== PERMISOS DEL ADMINISTRADOR ==============================

        if es_administrador(usuario_actual):

            if rol and rol.nombre == "Super Administrador":
                errores.append(
                    "Un Administrador no puede asignar el rol de Super Administrador."
                )

        # ============================== PROTECCIÓN CONTRA AUTO-DESACTIVACIÓN ==============================

        if usuario.id == usuario_actual.id and not activo:
            errores.append(
                "No puedes desactivar tu propia cuenta."
            )

        # ============================== GUARDAR CAMBIOS ==============================

        if not errores:

            with transaction.atomic():

                usuario.nombre = nombre
                usuario.nombre2 = nombre2
                usuario.apellido = apellido
                usuario.apellido2 = apellido2
                usuario.email = email
                usuario.telefono = telefono_normalizado
                usuario.rut = rut_normalizado
                usuario.rol = rol
                usuario.condominio = condominio
                usuario.activo = activo

                usuario.save()

                if usuario.user:
                    usuario.user.email = email
                    usuario.user.is_active = activo

                    usuario.user.save(
                        update_fields=[
                            "email",
                            "is_active"
                        ]
                    )

            return redirect("usuarios_lista")

        return render(request, "core/usuarios/editar.html", {
            "usuario_actual": usuario_actual,
            "usuario": usuario,
            "roles": roles,
            "condominios": condominios,
            "errores": errores,
        })

    return render(request, "core/usuarios/editar.html", {
        "usuario_actual": usuario_actual,
        "usuario": usuario,
        "roles": roles,
        "condominios": condominios,
    })


# ============================== CAMBIAR ESTADO DE USUARIO ==============================

@login_required
def usuario_cambiar_estado(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related(
            "rol",
            "condominio",
            "user"
        ),
        id=usuario_id
    )

    if usuario.id == usuario_actual.id:
        return redirect("usuarios_lista")

    if es_administrador(usuario_actual):

        if usuario.condominio_id != usuario_actual.condominio_id:
            return redirect("usuarios_lista")

        if es_super_administrador(usuario):
            return redirect("usuarios_lista")

    usuario.activo = not usuario.activo
    usuario.save(update_fields=["activo"])

    if usuario.user:
        usuario.user.is_active = usuario.activo
        usuario.user.save(
            update_fields=["is_active"]
        )

    return redirect("usuarios_lista")


# ============================== CONDOMINIOS ==============================

@login_required
def condominios_lista(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    if es_super_administrador(usuario_actual):
        condominios = Condominio.objects.filter(
            activo=True
        )

    else:
        condominios = Condominio.objects.filter(
            id=usuario_actual.condominio_id,
            activo=True
        )

    return render(request, "core/condominios/lista.html", {
        "usuario_actual": usuario_actual,
        "condominios": condominios,
    })


# ============================== CREAR CONDOMINIO ==============================

@login_required
def condominio_crear(request):
    usuario_actual = obtener_usuario(request)

    if not es_super_administrador(usuario_actual):
        return redirect("condominios_lista")

    ciudades = Ciudad.objects.all()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_id = request.POST.get("ciudad", "").strip()
        edificios = request.POST.get("edificios", "1").strip()
        portada = request.FILES.get("portada")

        errores = []

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del condominio."
            )

        if not direccion:
            errores.append(
                "Debes ingresar la dirección."
            )

        if not ciudad_id:
            errores.append(
                "Debes seleccionar una ciudad."
            )

        try:
            edificios_numero = int(edificios)

            if edificios_numero < 1:
                errores.append(
                    "El condominio debe tener al menos un edificio."
                )

        except ValueError:
            edificios_numero = 1
            errores.append(
                "La cantidad de edificios no es válida."
            )

        ciudad = None

        if ciudad_id:
            try:
                ciudad = Ciudad.objects.get(
                    id=int(ciudad_id)
                )

            except (Ciudad.DoesNotExist, ValueError):
                errores.append(
                    "La ciudad seleccionada no es válida."
                )

        if not errores:

            Condominio.objects.create(
                nombre=nombre,
                direccion=direccion,
                ciudad=ciudad,
                edificios=edificios_numero,
                portada=portada,
                activo=True,
            )

            return redirect("condominios_lista")

        return render(request, "core/condominios/crear.html", {
            "usuario_actual": usuario_actual,
            "ciudades": ciudades,
            "errores": errores,
            "datos": request.POST,
        })

    return render(request, "core/condominios/crear.html", {
        "usuario_actual": usuario_actual,
        "ciudades": ciudades,
    })


# ============================== EDITAR CONDOMINIO ==============================

@login_required
def condominio_editar(request, condominio_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    ciudades = Ciudad.objects.all()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_id = request.POST.get("ciudad", "").strip()
        activo = request.POST.get("activo") == "on"
        portada = request.FILES.get("portada")

        errores = []

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del condominio."
            )

        if not direccion:
            errores.append(
                "Debes ingresar la dirección."
            )

        ciudad = None

        if ciudad_id:

            try:
                ciudad = Ciudad.objects.get(
                    id=int(ciudad_id)
                )

            except (Ciudad.DoesNotExist, ValueError):
                errores.append(
                    "La ciudad seleccionada no es válida."
                )

        else:
            errores.append(
                "Debes seleccionar una ciudad."
            )

        if not errores:

            condominio.nombre = nombre
            condominio.direccion = direccion
            condominio.ciudad = ciudad
            condominio.activo = activo

            if portada:
                condominio.portada = portada

            condominio.save()

            return redirect("condominios_lista")

        return render(request, "core/condominios/editar.html", {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
            "ciudades": ciudades,
            "errores": errores,
        })

    return render(request, "core/condominios/editar.html", {
        "usuario_actual": usuario_actual,
        "condominio": condominio,
        "ciudades": ciudades,
    })


# ============================== EDIFICIOS DE UN CONDOMINIO ==============================

@login_required
def edificios_lista(request, condominio_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    edificios = condominio.edificios_set.all().prefetch_related(
        "unidades"
    )

    return render(request, "core/condominios/edificios.html", {
        "usuario_actual": usuario_actual,
        "condominio": condominio,
        "edificios": edificios,
    })


# ============================== EDITAR EDIFICIO ==============================

@login_required
def edificio_editar(request, edificio_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    edificio = get_object_or_404(
        Edificio.objects.select_related("condominio"),
        id=edificio_id
    )

    condominio = edificio.condominio

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        numero_pisos = request.POST.get(
            "numero_pisos",
            ""
        ).strip()
        viviendas_por_piso = request.POST.get(
            "viviendas_por_piso",
            ""
        ).strip()
        subterraneo = request.POST.get(
            "subterraneo"
        ) == "on"

        errores = []

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del edificio."
            )

        try:
            pisos = int(numero_pisos)

            if pisos < 2 or pisos > 20:
                errores.append(
                    "El número de pisos debe estar entre 2 y 20."
                )

        except ValueError:
            pisos = None
            errores.append(
                "El número de pisos no es válido."
            )

        try:
            viviendas = int(viviendas_por_piso)

            if viviendas < 1:
                errores.append(
                    "Debe existir al menos una vivienda por piso."
                )

        except ValueError:
            viviendas = None
            errores.append(
                "La cantidad de viviendas no es válida."
            )

        if not errores:

            edificio.nombre = nombre
            edificio.numero_pisos = pisos
            edificio.viviendas_por_piso = viviendas
            edificio.subterraneo = subterraneo

            edificio.save()

            return redirect(
                "edificios_lista",
                condominio_id=condominio.id
            )

        return render(
            request,
            "core/condominios/edificios_editar.html",
            {
                "usuario": usuario_actual,
                "edificio": edificio,
                "condominio": condominio,
                "errores": errores,
            }
        )

    return render(
        request,
        "core/condominios/edificios_editar.html",
        {
            "usuario": usuario_actual,
            "edificio": edificio,
            "condominio": condominio,
        }
    )


# ============================== UNIDADES DE UN EDIFICIO ==============================

@login_required
def unidades_lista(request, edificio_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    edificio = get_object_or_404(
        Edificio.objects.select_related("condominio"),
        id=edificio_id
    )

    if es_administrador(usuario_actual):

        if edificio.condominio_id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    unidades = edificio.unidades.select_related(
        "dueno",
        "arrendatario"
    ).order_by(
        "piso",
        "posicion"
    )

    return render(
        request,
        "core/condominios/unidades.html",
        {
            "usuario_actual": usuario_actual,
            "edificio": edificio,
            "condominio": edificio.condominio,
            "unidades": unidades,
        }
    )


# ============================== EDITAR UNIDAD ==============================

@login_required
def unidad_editar(request, unidad_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    unidad = get_object_or_404(
        Unidad.objects.select_related(
            "edificio__condominio",
            "dueno",
            "arrendatario"
        ),
        id=unidad_id
    )

    condominio = unidad.edificio.condominio

    if es_administrador(usuario_actual):

        if usuario_actual.condominio_id != condominio.id:
            return redirect("condominios_lista")

    usuarios = Usuario.objects.filter(
        condominio=condominio,
        activo=True,
        rol__nombre="Residente"
    ).select_related(
        "rol"
    ).order_by(
        "apellido",
        "nombre"
    )

    if request.method == "POST":
        dueno_id = request.POST.get(
            "dueno_id",
            ""
        ).strip()

        arrendatario_id = request.POST.get(
            "arrendatario_id",
            ""
        ).strip()

        errores = []
        dueno = None
        arrendatario = None

        if dueno_id:

            try:
                dueno = Usuario.objects.get(
                    id=int(dueno_id),
                    condominio=condominio,
                    activo=True,
                    rol__nombre="Residente"
                )

            except (
                Usuario.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "El propietario seleccionado no es válido."
                )

        if arrendatario_id:

            try:
                arrendatario = Usuario.objects.get(
                    id=int(arrendatario_id),
                    condominio=condominio,
                    activo=True,
                    rol__nombre="Residente"
                )

            except (
                Usuario.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "El arrendatario seleccionado no es válido."
                )

        if not errores:

            unidad.dueno = dueno
            unidad.arrendatario = arrendatario

            unidad.save()

            return redirect(
                "unidades_lista",
                edificio_id=unidad.edificio.id
            )

        return render(
            request,
            "core/condominios/unidad_editar.html",
            {
                "usuario_actual": usuario_actual,
                "unidad": unidad,
                "usuarios": usuarios,
                "errores": errores,
            }
        )

    return render(
        request,
        "core/condominios/unidad_editar.html",
        {
            "usuario_actual": usuario_actual,
            "unidad": unidad,
            "usuarios": usuarios,
        }
    )


# ============================== ÁREAS COMUNES ==============================

@login_required
def areas_comunes_lista(request, condominio_id):
    """
    Muestra las áreas comunes de un condominio.
    Solo pueden acceder Administradores y
    Super Administradores.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    # El Administrador solamente puede gestionar
    # las áreas de su propio condominio.
    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    areas = AreaComun.objects.filter(
        condominio=condominio
    ).prefetch_related(
        "horarios"
    ).order_by(
        "nombre"
    )

    return render(
        request,
        "core/areas_comunes/lista.html",
        {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
            "areas": areas,
        }
    )


# ============================== CREAR ÁREA COMÚN ==============================

@login_required
def area_comun_crear(request, condominio_id):
    """
    Permite crear un área común dentro de un condominio.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    if request.method == "POST":

        nombre = request.POST.get(
            "nombre",
            ""
        ).strip()

        descripcion = request.POST.get(
            "descripcion",
            ""
        ).strip()

        capacidad = request.POST.get(
            "capacidad",
            ""
        ).strip()

        reservable = request.POST.get(
            "reservable"
        ) == "on"

        errores = []

        # ============================== VALIDAR NOMBRE ==============================

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del área común."
            )

        # ============================== VALIDAR CAPACIDAD ==============================

        capacidad_numero = None

        if capacidad:

            try:
                capacidad_numero = int(capacidad)

                if capacidad_numero < 1:
                    errores.append(
                        "La capacidad debe ser mayor a 0."
                    )

            except ValueError:
                errores.append(
                    "La capacidad debe ser un número válido."
                )

        # ============================== VALIDAR NOMBRE DUPLICADO ==============================

        if AreaComun.objects.filter(
            condominio=condominio,
            nombre__iexact=nombre
        ).exists():

            errores.append(
                "Ya existe un área común con ese nombre en este condominio."
            )

        # ============================== CREAR ==============================

        if not errores:

            AreaComun.objects.create(
                condominio=condominio,
                nombre=nombre,
                descripcion=descripcion,
                capacidad=capacidad_numero,
                reservable=reservable,
            )

            return redirect(
                "areas_comunes_lista",
                condominio_id=condominio.id
            )

        return render(
            request,
            "core/areas_comunes/crear.html",
            {
                "usuario_actual": usuario_actual,
                "condominio": condominio,
                "errores": errores,
                "datos": request.POST,
            }
        )

    return render(
        request,
        "core/areas_comunes/crear.html",
        {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
        }
    )


# ============================== EDITAR ÁREA COMÚN ==============================

@login_required
def area_comun_editar(request, area_id):
    """
    Permite modificar un área común.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    area = get_object_or_404(
        AreaComun.objects.select_related(
            "condominio"
        ),
        id=area_id
    )

    condominio = area.condominio

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    if request.method == "POST":

        nombre = request.POST.get(
            "nombre",
            ""
        ).strip()

        descripcion = request.POST.get(
            "descripcion",
            ""
        ).strip()

        capacidad = request.POST.get(
            "capacidad",
            ""
        ).strip()

        reservable = request.POST.get(
            "reservable"
        ) == "on"

        errores = []

        # ============================== VALIDAR NOMBRE ==============================

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del área común."
            )

        # ============================== VALIDAR CAPACIDAD ==============================

        capacidad_numero = None

        if capacidad:

            try:
                capacidad_numero = int(capacidad)

                if capacidad_numero < 1:
                    errores.append(
                        "La capacidad debe ser mayor a 0."
                    )

            except ValueError:
                errores.append(
                    "La capacidad debe ser un número válido."
                )

        # ============================== VALIDAR DUPLICADO ==============================

        if AreaComun.objects.filter(
            condominio=condominio,
            nombre__iexact=nombre
        ).exclude(
            id=area.id
        ).exists():

            errores.append(
                "Ya existe otra área común con ese nombre en este condominio."
            )

        # ============================== GUARDAR ==============================

        if not errores:

            area.nombre = nombre
            area.descripcion = descripcion
            area.capacidad = capacidad_numero
            area.reservable = reservable

            area.save()

            return redirect(
                "areas_comunes_lista",
                condominio_id=condominio.id
            )

        return render(
            request,
            "core/areas_comunes/editar.html",
            {
                "usuario_actual": usuario_actual,
                "condominio": condominio,
                "area": area,
                "errores": errores,
            }
        )

    return render(
        request,
        "core/areas_comunes/editar.html",
        {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
            "area": area,
        }
    )


# ============================== CONFIGURAR HORARIOS ==============================

@login_required
def area_comun_horarios(request, area_id):
    """
    Permite agregar y eliminar horarios de un área común.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    area = get_object_or_404(
        AreaComun.objects.select_related(
            "condominio"
        ),
        id=area_id
    )

    condominio = area.condominio

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    horarios = area.horarios.all()

    if request.method == "POST":

        dia_semana = request.POST.get(
            "dia_semana",
            ""
        ).strip()

        hora_inicio = request.POST.get(
            "hora_inicio",
            ""
        ).strip()

        hora_fin = request.POST.get(
            "hora_fin",
            ""
        ).strip()

        errores = []

        # ============================== VALIDAR DÍA ==============================

        dia_numero = None

        try:
            dia_numero = int(dia_semana)

            if dia_numero < 0 or dia_numero > 6:
                errores.append(
                    "El día seleccionado no es válido."
                )

        except ValueError:
            errores.append(
                "Debes seleccionar un día válido."
            )

        # ============================== VALIDAR HORAS ==============================

        if not hora_inicio:
            errores.append(
                "Debes indicar la hora de inicio."
            )

        if not hora_fin:
            errores.append(
                "Debes indicar la hora de término."
            )

        # ============================== CREAR HORARIO ==============================

        horario = None

        if not errores:

            horario = HorarioAreaComun(
                area_comun=area,
                dia_semana=dia_numero,
                hora_inicio=hora_inicio,
                hora_fin=hora_fin,
            )

            try:
                horario.full_clean()

            except ValidationError as error:
                errores.extend(error.messages)

        # ============================== VALIDAR CRUCE DE HORARIOS ==============================

        if not errores and horario:

            horarios_existentes = HorarioAreaComun.objects.filter(
                area_comun=area,
                dia_semana=dia_numero,
                hora_inicio__lt=horario.hora_fin,
                hora_fin__gt=horario.hora_inicio,
            )

            if horarios_existentes.exists():

                errores.append(
                    "El horario se cruza con otro horario existente para ese día."
                )

        # ============================== GUARDAR ==============================

        if not errores:

            horario.save()

            return redirect(
                "area_comun_horarios",
                area_id=area.id
            )

        horarios = area.horarios.all()

        return render(
            request,
            "core/areas_comunes/horarios.html",
            {
                "usuario_actual": usuario_actual,
                "condominio": condominio,
                "area": area,
                "horarios": horarios,
                "errores": errores,
                "datos": request.POST,
            }
        )

    return render(
        request,
        "core/areas_comunes/horarios.html",
        {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
            "area": area,
            "horarios": horarios,
        }
    )


# ============================== ACTUALIZAR HORARIO ==============================

@login_required
def area_comun_horario_actualizar(request, horario_id):
    """
    Actualiza el día y horario de un bloque desde
    el calendario interactivo mediante arrastrar y soltar.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return JsonResponse(
            {
                "ok": False,
                "error": "No tienes permisos para realizar esta acción."
            },
            status=403
        )

    horario = get_object_or_404(
        HorarioAreaComun.objects.select_related(
            "area_comun__condominio"
        ),
        id=horario_id
    )

    area = horario.area_comun
    condominio = area.condominio

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "No puedes modificar horarios de este condominio."
                },
                status=403
            )

    if request.method != "POST":
        return JsonResponse(
            {
                "ok": False,
                "error": "Método no permitido."
            },
            status=405
        )

    try:
        data = json.loads(request.body)

        dia_numero = int(
            data.get("dia_semana")
        )

        hora_inicio = time.fromisoformat(
            data.get("hora_inicio")
        )

        hora_fin = time.fromisoformat(
            data.get("hora_fin")
        )

    except (
        TypeError,
        ValueError,
        json.JSONDecodeError
    ):
        return JsonResponse(
            {
                "ok": False,
                "error": "Los datos del horario no son válidos."
            },
            status=400
        )

    # ============================== VALIDAR DÍA ==============================

    if dia_numero < 0 or dia_numero > 6:
        return JsonResponse(
            {
                "ok": False,
                "error": "El día seleccionado no es válido."
            },
            status=400
        )

    # ============================== VALIDAR HORAS ==============================

    if hora_fin <= hora_inicio:
        return JsonResponse(
            {
                "ok": False,
                "error": "La hora de término debe ser posterior a la hora de inicio."
            },
            status=400
        )

    # ============================== VALIDAR CRUCE ==============================

    existe_cruce = HorarioAreaComun.objects.filter(
        area_comun=area,
        dia_semana=dia_numero,
        hora_inicio__lt=hora_fin,
        hora_fin__gt=hora_inicio,
    ).exclude(
        id=horario.id
    ).exists()

    if existe_cruce:
        return JsonResponse(
            {
                "ok": False,
                "error": "El horario se cruza con otro horario existente para ese día."
            },
            status=409
        )

    # ============================== ACTUALIZAR ==============================

    horario.dia_semana = dia_numero
    horario.hora_inicio = hora_inicio
    horario.hora_fin = hora_fin

    try:
        horario.full_clean()

    except ValidationError as error:
        return JsonResponse(
            {
                "ok": False,
                "error": " ".join(error.messages)
            },
            status=400
        )

    horario.save(
        update_fields=[
            "dia_semana",
            "hora_inicio",
            "hora_fin",
        ]
    )

    return JsonResponse(
        {
            "ok": True,
            "dia_semana": horario.dia_semana,
            "hora_inicio": horario.hora_inicio.strftime("%H:%M"),
            "hora_fin": horario.hora_fin.strftime("%H:%M"),
        }
    )


# ============================== ELIMINAR HORARIO ==============================

@login_required
def area_comun_horario_eliminar(request, horario_id):
    """
    Elimina un horario de un área común.
    """

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    horario = get_object_or_404(
        HorarioAreaComun.objects.select_related(
            "area_comun__condominio"
        ),
        id=horario_id
    )

    area = horario.area_comun
    condominio = area.condominio

    if es_administrador(usuario_actual):

        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    if request.method == "POST":

        horario.delete()

    return redirect(
        "area_comun_horarios",
        area_id=area.id
    )


# ============================== PÁGINA PÚBLICA ==============================

def landing(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    return render(
        request,
        "core/landing.html"
    )
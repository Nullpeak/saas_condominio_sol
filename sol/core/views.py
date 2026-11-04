import json
from types import SimpleNamespace
from datetime import time, datetime, timedelta

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.models import User
from django.contrib.auth.views import (PasswordResetView,PasswordResetDoneView,PasswordResetConfirmView,PasswordResetCompleteView,)
from django.urls import reverse_lazy
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils import timezone
from launch.guards import setup_disponible
from launch.ciudades import ciudades_chile

from .validators import *
from .models import *


# ============================== FUNCIONES AUXILIARES ==============================

def obtener_usuario(request):
    return request.user.usuario


def tiene_rol(usuario, *roles):
    return usuario.rol in roles


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


def lista_roles():
    # Los roles ahora son opciones de texto del modelo; se entregan con .id y .nombre
    # para que las plantillas que recorren "roles" sigan funcionando.
    return [SimpleNamespace(id=valor, nombre=valor) for valor in Usuario.Rol.values]


def resolver_username(identificador):
    """
    Permite iniciar sesión con el nombre de usuario o con el RUT.
    Un username exacto tiene prioridad; si no existe y el texto es un RUT válido,
    se busca el Usuario con ese RUT y se devuelve el username de su cuenta.
    """
    if User.objects.filter(username=identificador).exists():
        return identificador

    try:
        validar_rut(identificador)
    except ValidationError:
        return identificador

    perfil = Usuario.objects.select_related("user").filter(
        rut=formatear_rut(identificador),
        user__isnull=False
    ).first()

    return perfil.user.username if perfil else identificador


# ============================== AUTENTICACIÓN ==============================

def login_view(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        usuario_django = authenticate(
            request,
            username=resolver_username(username),
            password=password
        )

        if usuario_django is not None:
            try:
                usuario = Usuario.objects.select_related(
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

    condominios = usuario.condominios_asignados().filter(activo=True)
    usuarios = Usuario.objects.select_related("user", "condominio").filter(
        Q(condominio__in=condominios) | Q(condominios_administrados__in=condominios)
    ).distinct()

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
        usuarios = Usuario.objects.select_related("user", "condominio").all()
    else:
        condominios = usuario_actual.condominios_asignados()
        usuarios = Usuario.objects.select_related("user", "condominio").filter(
            Q(condominio__in=condominios) | Q(condominios_administrados__in=condominios)
        ).distinct()

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

    roles = lista_roles()
    ciudades_sugeridas = ciudades_chile() if es_super_administrador(usuario_actual) else []

    condominios = usuario_actual.condominios_asignados().filter(activo=True)

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
        condominios_administrados_ids = request.POST.getlist("condominios_administrados")
        crear_condominio = (
            es_super_administrador(usuario_actual)
            and request.POST.get("crear_condominio") == "on"
        )
        nombre_condominio = request.POST.get("nombre_condominio", "").strip()
        direccion_condominio = request.POST.get("direccion_condominio", "").strip()
        ciudad_condominio = request.POST.get("ciudad_condominio", "").strip()
        edificios_condominio = request.POST.get("edificios_condominio", "1").strip()
        es_propietario = request.POST.get("es_propietario", "")

        errores = []

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

        telefono_normalizado = None

        if telefono:
            try:
                validar_telefono(telefono)
                telefono_normalizado = formatear_telefono(telefono)

            except ValidationError as error:
                errores.extend(error.messages)

        if username and User.objects.filter(
            username=username
        ).exists():
            errores.append(
                "El nombre de usuario ya está registrado."
            )

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

        rol = None

        if rol_id:
            if rol_id in Usuario.Rol.values:
                rol = rol_id

            else:
                errores.append(
                    "El rol seleccionado no es válido."
                )

        condominio = None

        if condominio_id:
            try:
                condominio = Condominio.objects.get(
                    id=int(condominio_id),
                    activo=True,
                )

            except (Condominio.DoesNotExist, ValueError):
                errores.append(
                    "El condominio seleccionado no es válido."
                )

        if es_administrador(usuario_actual) and rol == Usuario.Rol.SUPER:
            errores.append("Un Administrador no puede crear un Super Administrador.")

        if condominio and not usuario_actual.puede_acceder_condominio(condominio.id):
            errores.append("No tienes permiso para crear usuarios en ese condominio.")

        if rol == Usuario.Rol.ADMINISTRADOR:
            if crear_condominio:
                condominios_administrados_ids = []
            elif not condominios_administrados_ids and condominio:
                condominios_administrados_ids = [str(condominio.id)]
            if not crear_condominio:
                asignaciones = list(condominios.filter(pk__in=condominios_administrados_ids))
                if not asignaciones or len(asignaciones) != len(set(condominios_administrados_ids)):
                    errores.append("Selecciona uno o más condominios para el Administrador.")
                elif not condominio or condominio.id not in {item.id for item in asignaciones}:
                    condominio = asignaciones[0]

        if es_super_administrador(usuario_actual) and rol != Usuario.Rol.SUPER:
            if crear_condominio:
                if not nombre_condominio:
                    errores.append("Ingresa el nombre del nuevo condominio.")
                if not direccion_condominio:
                    errores.append("Ingresa la dirección del nuevo condominio.")
                if not ciudad_condominio:
                    errores.append("Indica la ciudad del nuevo condominio.")
                try:
                    edificios_condominio_numero = int(edificios_condominio)
                    if edificios_condominio_numero < 1:
                        raise ValueError
                except ValueError:
                    edificios_condominio_numero = 1
                    errores.append("El condominio debe tener al menos un edificio.")
            elif not condominio and rol != Usuario.Rol.ADMINISTRADOR:
                errores.append("Selecciona el condominio al que pertenece este usuario o crea uno nuevo.")
        elif crear_condominio:
            errores.append("Solo puedes asociar un condominio nuevo a una cuenta que pertenezca a un condominio.")

        unidad_residente = None
        if rol == Usuario.Rol.RESIDENTE:
            if crear_condominio:
                errores.append("Primero crea las unidades del condominio y luego registra al residente.")
            elif not condominio:
                errores.append("Selecciona el condominio del residente.")
            else:
                edificios_condominio = condominio.edificios_set.order_by("numero", "id")
                edificio_seleccionado = None
                if edificios_condominio.count() == 1:
                    edificio_seleccionado = edificios_condominio.first()
                else:
                    edificio_id = request.POST.get("edificio", "").strip()
                    try:
                        edificio_seleccionado = edificios_condominio.filter(pk=int(edificio_id)).first()
                    except (TypeError, ValueError):
                        edificio_seleccionado = None
                    if not edificio_seleccionado:
                        errores.append("Selecciona el edificio del residente.")

                if edificio_seleccionado:
                    if es_propietario not in ("si", "no"):
                        errores.append("Indica si el residente es propietario de la unidad.")
                    campo_relacion = "dueno__isnull" if es_propietario == "si" else "arrendatario__isnull"
                    unidades_disponibles = Unidad.objects.filter(edificio=edificio_seleccionado, **{campo_relacion: True}).order_by("numero_unidad")
                    unidad_id = request.POST.get("unidad", "").strip()
                    try:
                        unidad_residente = unidades_disponibles.filter(pk=int(unidad_id)).first()
                    except (TypeError, ValueError):
                        unidad_residente = None
                    if not unidad_residente:
                        if not unidades_disponibles.exists():
                            errores.append("El edificio no tiene unidades disponibles para asignar.")
                        else:
                            errores.append("Selecciona una unidad disponible para el residente.")

        if not errores:

            with transaction.atomic():

                if crear_condominio:
                    ciudad = Ciudad.objects.filter(
                        nombre__iexact=ciudad_condominio
                    ).first()
                    if not ciudad:
                        ciudad = Ciudad.objects.create(nombre=ciudad_condominio)
                    condominio = Condominio.objects.create(
                        nombre=nombre_condominio,
                        direccion=direccion_condominio,
                        ciudad=ciudad,
                        edificios=edificios_condominio_numero,
                        activo=True,
                    )

                user = User.objects.create_user(
                    username=username,
                    password=password,
                    email=email
                )

                nuevo_usuario = Usuario.objects.create(
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

                if rol == Usuario.Rol.ADMINISTRADOR:
                    if crear_condominio:
                        asignaciones = [condominio]
                    else:
                        asignaciones = list(condominios.filter(pk__in=condominios_administrados_ids))
                        if not asignaciones:
                            asignaciones = [condominio]
                    nuevo_usuario.condominios_administrados.set(asignaciones)

                if rol == Usuario.Rol.RESIDENTE:
                    if es_propietario == "si":
                        unidad_residente.dueno = nuevo_usuario
                        unidad_residente.propietario_vive = True
                        unidad_residente.save(update_fields=["dueno", "propietario_vive"])
                    else:
                        unidad_residente.arrendatario = nuevo_usuario
                        unidad_residente.save(update_fields=["arrendatario"])

            return redirect("usuarios_lista")

        return render(request, "core/usuarios/crear.html", {
            "usuario": usuario_actual,
            "roles": roles,
            "condominios": condominios,
            "ciudades_sugeridas": ciudades_sugeridas,
            "errores": errores,
            "datos": request.POST,
            "condominios_seleccionados": request.POST.getlist("condominios_administrados"),
        })

    return render(request, "core/usuarios/crear.html", {
        "usuario": usuario_actual,
        "roles": roles,
        "condominios": condominios,
        "ciudades_sugeridas": ciudades_sugeridas,
        "condominios_seleccionados": [],
    })


@login_required
def usuario_unidades_disponibles(request):
    usuario_actual = obtener_usuario(request)
    if not puede_gestionar_usuarios(usuario_actual):
        return JsonResponse({"edificios": []}, status=403)

    try:
        condominio_id = int(request.GET.get("condominio", ""))
    except (TypeError, ValueError):
        return JsonResponse({"edificios": []})

    condominio = Condominio.objects.filter(pk=condominio_id, activo=True).first()
    if not condominio or not usuario_actual.puede_acceder_condominio(condominio.id):
        return JsonResponse({"edificios": []})

    es_propietario = request.GET.get("es_propietario", "")
    if es_propietario not in ("si", "no"):
        return JsonResponse({"edificios": []})
    campo_relacion = "dueno__isnull" if es_propietario == "si" else "arrendatario__isnull"
    edificios = []
    for edificio in condominio.edificios_set.order_by("numero", "id"):
        unidades = edificio.unidades.filter(**{campo_relacion: True}).order_by("numero_unidad")
        edificios.append({
            "id": edificio.id,
            "nombre": edificio.nombre,
            "unidades": [{"id": unidad.id, "numero": unidad.numero_unidad} for unidad in unidades],
        })
    return JsonResponse({"edificios": edificios})


# ============================== EDITAR USUARIO ==============================

@login_required
def usuario_editar(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related(
            "user",
            "condominio"
        ),
        id=usuario_id
    )

    if es_administrador(usuario_actual):
        if not usuario_actual.condominios_asignados().filter(
            pk__in=usuario.condominios_asignados().values("id")
        ).exists():
            return redirect("usuarios_lista")

        if es_super_administrador(usuario):
            return redirect("usuarios_lista")

    roles = lista_roles()

    condominios = usuario_actual.condominios_asignados().filter(activo=True)

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
        condominios_administrados_ids = request.POST.getlist("condominios_administrados")
        activo = request.POST.get("activo") == "on"

        errores = []

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

        telefono_normalizado = None

        if telefono:
            try:
                validar_telefono(telefono)
                telefono_normalizado = formatear_telefono(telefono)

            except ValidationError as error:
                errores.extend(error.messages)

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

        rol = None

        if not rol_id:
            errores.append(
                "Debes seleccionar un rol."
            )

        elif rol_id in Usuario.Rol.values:
            rol = rol_id

        else:
            errores.append(
                "El rol seleccionado no es válido."
            )

        condominio = None
        asignaciones = []
        if rol == Usuario.Rol.ADMINISTRADOR:
            if not condominios_administrados_ids and condominio_id:
                condominios_administrados_ids = [condominio_id]
            asignaciones = list(condominios.filter(pk__in=condominios_administrados_ids))
            if not asignaciones or len(asignaciones) != len(set(condominios_administrados_ids)):
                errores.append("Selecciona uno o más condominios para el Administrador.")
            else:
                condominio = asignaciones[0]
        elif condominio_id:
            try:
                condominio = condominios.get(id=int(condominio_id))
            except (Condominio.DoesNotExist, ValueError):
                errores.append("El condominio seleccionado no es válido o no está asignado a tu cuenta.")

        if rol and rol != Usuario.Rol.SUPER and not condominio:
            errores.append("Selecciona un condominio para este usuario.")

        if es_administrador(usuario_actual):

            if rol == Usuario.Rol.SUPER:
                errores.append(
                    "Un Administrador no puede asignar el rol de Super Administrador."
                )

        if usuario.id == usuario_actual.id and not activo:
            errores.append(
                "No puedes desactivar tu propia cuenta."
            )

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

                if rol == Usuario.Rol.ADMINISTRADOR:
                    usuario.condominios_administrados.set(asignaciones)
                else:
                    usuario.condominios_administrados.clear()

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
            "datos": request.POST,
            "condominios_seleccionados": condominios_administrados_ids,
            "condominios_asignados_usuario": list(usuario.condominios_administrados.values_list("id", flat=True)),
        })

    return render(request, "core/usuarios/editar.html", {
        "usuario_actual": usuario_actual,
        "usuario": usuario,
        "roles": roles,
        "condominios": condominios,
        "condominios_asignados_usuario": list(usuario.condominios_administrados.values_list("id", flat=True)),
    })


# ============================== CAMBIAR ESTADO DE USUARIO ==============================

@login_required
def usuario_cambiar_estado(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related(
            "condominio",
            "user"
        ),
        id=usuario_id
    )

    if usuario.id == usuario_actual.id:
        return redirect("usuarios_lista")

    if es_administrador(usuario_actual):

        if not usuario_actual.condominios_asignados().filter(
            pk__in=usuario.condominios_asignados().values("id")
        ).exists():
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

    condominios = usuario_actual.condominios_asignados().filter(activo=True)

    return render(request, "core/condominios/lista.html", {
        "usuario": usuario_actual,
        "usuario_actual": usuario_actual,
        "condominios": condominios,
    })


# ============================== CREAR CONDOMINIO ==============================

@login_required
def condominio_crear(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("condominios_lista")

    ciudades_sugeridas = ciudades_chile()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_nombre = request.POST.get("ciudad", "").strip()
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

        if not ciudad_nombre:
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

        if not errores:
            with transaction.atomic():
                ciudad = Ciudad.objects.filter(nombre__iexact=ciudad_nombre).first()
                if not ciudad:
                    ciudad = Ciudad.objects.create(nombre=ciudad_nombre)
                condominio = Condominio.objects.create(
                    nombre=nombre,
                    direccion=direccion,
                    ciudad=ciudad,
                    edificios=edificios_numero,
                    portada=portada,
                    activo=True,
                )
                if es_administrador(usuario_actual):
                    usuario_actual.condominios_administrados.add(condominio)

            return redirect("condominios_lista")

        return render(request, "core/condominios/crear.html", {
            "usuario_actual": usuario_actual,
            "ciudades_sugeridas": ciudades_sugeridas,
            "errores": errores,
            "datos": request.POST,
        })

    return render(request, "core/condominios/crear.html", {
        "usuario_actual": usuario_actual,
        "ciudades_sugeridas": ciudades_sugeridas,
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
            return redirect("condominios_lista")

    ciudades = Ciudad.objects.all()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_id = request.POST.get("ciudad", "").strip()
        edificios_texto = request.POST.get("edificios", "").strip()
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

        try:
            cantidad_edificios = int(edificios_texto)
            if cantidad_edificios < 1:
                raise ValueError
        except (TypeError, ValueError):
            cantidad_edificios = condominio.edificios_set.count()
            errores.append("El condominio debe tener al menos un edificio.")

        edificios_actuales = list(condominio.edificios_set.order_by("-numero", "-id"))
        cantidad_actual = len(edificios_actuales)
        edificios_a_eliminar = edificios_actuales[:max(0, cantidad_actual - cantidad_edificios)]
        if any(edificio.unidades.exists() for edificio in edificios_a_eliminar):
            errores.append("No se puede reducir la cantidad porque los edificios que se quitarían tienen unidades. Reasigna o elimina primero esas unidades.")

        if not errores:
            with transaction.atomic():
                if cantidad_edificios < cantidad_actual:
                    for edificio in edificios_a_eliminar:
                        edificio.delete()
                elif cantidad_edificios > cantidad_actual:
                    siguiente_numero = max((edificio.numero for edificio in edificios_actuales), default=0) + 1
                    for numero in range(siguiente_numero, siguiente_numero + cantidad_edificios - cantidad_actual):
                        Edificio.objects.create(
                            condominio=condominio,
                            numero=numero,
                            nombre=f"Edificio {numero}",
                        )

                condominio.nombre = nombre
                condominio.direccion = direccion
                condominio.ciudad = ciudad
                condominio.edificios = cantidad_edificios
                condominio.activo = activo

                if portada:
                    condominio.portada = portada

                condominio.save()

            return redirect("condominios_lista")

        return render(request, "core/condominios/editar.html", {
            "usuario_actual": usuario_actual,
            "condominio": condominio,
            "ciudades": ciudades,
            "cantidad_edificios": condominio.edificios_set.count(),
            "errores": errores,
            "datos": request.POST,
        })

    return render(request, "core/condominios/editar.html", {
        "usuario_actual": usuario_actual,
        "condominio": condominio,
        "ciudades": ciudades,
        "cantidad_edificios": condominio.edificios_set.count(),
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

        if not usuario_actual.puede_acceder_condominio(edificio.condominio_id):
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
            return redirect("condominios_lista")

    usuarios = Usuario.objects.filter(
        condominio=condominio,
        activo=True,
        rol=Usuario.Rol.RESIDENTE
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
                    rol=Usuario.Rol.RESIDENTE
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
                    rol=Usuario.Rol.RESIDENTE
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
            unidad.propietario_vive = bool(dueno and request.POST.get("propietario_vive") == "on")

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
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    if es_administrador(usuario_actual):

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(
        Condominio,
        id=condominio_id
    )

    if es_administrador(usuario_actual):

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

        requiere_reserva = request.POST.get(
            "requiere_reserva"
        ) == "on"

        permite_reservas_consecutivas = request.POST.get(
            "permite_reservas_consecutivas"
        ) == "on"

        duracion_maxima = request.POST.get(
            "duracion_maxima",
            ""
        ).strip()

        dias_anticipacion_maxima = request.POST.get(
            "dias_anticipacion_maxima",
            ""
        ).strip()

        errores = []

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del área común."
            )

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

        duracion_maxima_numero = None

        if duracion_maxima:

            try:
                duracion_maxima_numero = int(
                    duracion_maxima
                )

                if duracion_maxima_numero < 1:
                    errores.append(
                        "La duración máxima debe ser mayor a 0 horas."
                    )

            except ValueError:
                errores.append(
                    "La duración máxima debe ser un número válido."
                )

        dias_anticipacion_numero = None

        if dias_anticipacion_maxima:

            try:
                dias_anticipacion_numero = int(
                    dias_anticipacion_maxima
                )

                if dias_anticipacion_numero < 1:
                    errores.append(
                        "Los días de anticipación deben ser mayores a 0."
                    )

            except ValueError:
                errores.append(
                    "Los días de anticipación deben ser un número válido."
                )

        if AreaComun.objects.filter(
            condominio=condominio,
            nombre__iexact=nombre
        ).exists():

            errores.append(
                "Ya existe un área común con ese nombre en este condominio."
            )

        if not errores:

            AreaComun.objects.create(
                condominio=condominio,
                nombre=nombre,
                descripcion=descripcion,
                capacidad=capacidad_numero,
                reservable=reservable,
                requiere_reserva=requiere_reserva,
                permite_reservas_consecutivas=permite_reservas_consecutivas,
                duracion_maxima=duracion_maxima_numero,
                dias_anticipacion_maxima=dias_anticipacion_numero,
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

        requiere_reserva = request.POST.get(
            "requiere_reserva"
        ) == "on"

        permite_reservas_consecutivas = request.POST.get(
            "permite_reservas_consecutivas"
        ) == "on"

        duracion_maxima = request.POST.get(
            "duracion_maxima",
            ""
        ).strip()

        dias_anticipacion_maxima = request.POST.get(
            "dias_anticipacion_maxima",
            ""
        ).strip()

        errores = []

        if not nombre:
            errores.append(
                "Debes ingresar el nombre del área común."
            )

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

        duracion_maxima_numero = None

        if duracion_maxima:

            try:
                duracion_maxima_numero = int(
                    duracion_maxima
                )

                if duracion_maxima_numero < 1:
                    errores.append(
                        "La duración máxima debe ser mayor a 0 horas."
                    )

            except ValueError:
                errores.append(
                    "La duración máxima debe ser un número válido."
                )

        dias_anticipacion_numero = None

        if dias_anticipacion_maxima:

            try:
                dias_anticipacion_numero = int(
                    dias_anticipacion_maxima
                )

                if dias_anticipacion_numero < 1:
                    errores.append(
                        "Los días de anticipación deben ser mayores a 0."
                    )

            except ValueError:
                errores.append(
                    "Los días de anticipación deben ser un número válido."
                )

        if AreaComun.objects.filter(
            condominio=condominio,
            nombre__iexact=nombre
        ).exclude(
            id=area.id
        ).exists():

            errores.append(
                "Ya existe otra área común con ese nombre en este condominio."
            )

        if not errores:

            area.nombre = nombre
            area.descripcion = descripcion
            area.capacidad = capacidad_numero
            area.reservable = reservable
            area.requiere_reserva = requiere_reserva
            area.permite_reservas_consecutivas = (
                permite_reservas_consecutivas
            )
            area.duracion_maxima = duracion_maxima_numero
            area.dias_anticipacion_maxima = (
                dias_anticipacion_numero
            )

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
                "datos": request.POST,
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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

        if not hora_inicio:
            errores.append(
                "Debes indicar la hora de inicio."
            )

        if not hora_fin:
            errores.append(
                "Debes indicar la hora de término."
            )

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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
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

    if dia_numero < 0 or dia_numero > 6:
        return JsonResponse(
            {
                "ok": False,
                "error": "El día seleccionado no es válido."
            },
            status=400
        )

    if hora_fin <= hora_inicio:
        return JsonResponse(
            {
                "ok": False,
                "error": "La hora de término debe ser posterior a la hora de inicio."
            },
            status=400
        )

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

        if not usuario_actual.puede_acceder_condominio(condominio.id):
            return redirect("condominios_lista")

    if request.method == "POST":

        horario.delete()

    return redirect(
        "area_comun_horarios",
        area_id=area.id
    )


# ============================== CREAR RESERVACIÓN ==============================

@login_required
def reservacion_crear(request):
    """
    Permite crear una reserva para un área común.

    Se validan:
    - Que el área permita reservas.
    - Que el usuario pertenezca al condominio.
    - Que exista una unidad válida.
    - Que la fecha no esté en el pasado.
    - Que se respete la anticipación máxima.
    - Que la duración máxima no sea superada.
    - Que la reserva esté completamente dentro
      del horario configurado para el área.
    - Que no exista otra reserva que se cruce.
    """

    usuario_actual = obtener_usuario(request)

    if not usuario_actual.condominios_asignados().exists():
        return redirect("inicio")

    condominios = usuario_actual.condominios_asignados()

    # ============================== ÁREAS DISPONIBLES ==============================

    areas = AreaComun.objects.filter(
        condominio__in=condominios,
        reservable=True
    ).prefetch_related(
        "horarios"
    ).order_by(
        "nombre"
    )

    # ============================== UNIDADES DISPONIBLES ==============================

    if es_residente(usuario_actual):

        unidades = Unidad.objects.filter(
            edificio__condominio__in=condominios
        ).filter(
            dueno=usuario_actual
        ) | Unidad.objects.filter(
            edificio__condominio__in=condominios,
            arrendatario=usuario_actual
        )

        unidades = unidades.select_related(
            "edificio"
        ).distinct().order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    else:

        unidades = Unidad.objects.filter(
            edificio__condominio__in=condominios
        ).select_related(
            "edificio"
        ).order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    if request.method == "POST":

        area_id = request.POST.get(
            "area_comun",
            ""
        ).strip()

        unidad_id = request.POST.get(
            "unidad",
            ""
        ).strip()

        fecha_reserva_texto = request.POST.get(
            "fecha_reserva",
            ""
        ).strip()

        fin_reserva_texto = request.POST.get(
            "fin_reserva",
            ""
        ).strip()

        notas = request.POST.get(
            "notas",
            ""
        ).strip()

        errores = []

        # ============================== ÁREA ==============================

        area = None

        if not area_id:

            errores.append(
                "Debes seleccionar un área común."
            )

        else:

            try:
                area = AreaComun.objects.get(
                    id=int(area_id),
                    condominio__in=condominios
                )

            except (
                AreaComun.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "El área común seleccionada no es válida."
                )

        if area and not area.reservable:

            errores.append(
                "El área común seleccionada no permite reservas."
            )

        # ============================== UNIDAD ==============================

        unidad = None

        if not unidad_id:

            errores.append(
                "Debes seleccionar una unidad."
            )

        else:

            try:
                unidad = unidades.select_related("edificio", "edificio__condominio").filter(pk=int(unidad_id)).first()
                if not unidad:
                    raise Unidad.DoesNotExist

            except (
                Unidad.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "La unidad seleccionada no es válida."
                )

        if area and unidad and unidad.edificio.condominio_id != area.condominio_id:
            errores.append("La unidad seleccionada debe pertenecer al condominio del área.")

        # Los residentes solamente pueden reservar
        # usando una unidad de la que sean propietario
        # o arrendatario.
        if (
            unidad
            and es_residente(usuario_actual)
            and unidad.dueno_id != usuario_actual.id
            and unidad.arrendatario_id != usuario_actual.id
        ):
            errores.append(
                "Solo puedes realizar reservas usando una unidad de la que seas propietario o arrendatario."
            )

        # ============================== FECHAS ==============================

        fecha_reserva = None
        fin_reserva = None

        if not fecha_reserva_texto:

            errores.append(
                "Debes indicar la fecha y hora de inicio."
            )

        else:

            try:
                fecha_reserva = datetime.fromisoformat(
                    fecha_reserva_texto
                )

                if timezone.is_naive(fecha_reserva):
                    fecha_reserva = timezone.make_aware(
                        fecha_reserva
                    )

            except ValueError:

                errores.append(
                    "La fecha y hora de inicio no son válidas."
                )

        if not fin_reserva_texto:

            errores.append(
                "Debes indicar la fecha y hora de término."
            )

        else:

            try:
                fin_reserva = datetime.fromisoformat(
                    fin_reserva_texto
                )

                if timezone.is_naive(fin_reserva):
                    fin_reserva = timezone.make_aware(
                        fin_reserva
                    )

            except ValueError:

                errores.append(
                    "La fecha y hora de término no son válidas."
                )

        # ============================== VALIDAR ORDEN DE FECHAS ==============================

        if fecha_reserva and fin_reserva:

            if fin_reserva <= fecha_reserva:

                errores.append(
                    "La hora de término debe ser posterior a la hora de inicio."
                )

            if fecha_reserva.date() != fin_reserva.date():

                errores.append(
                    "La reserva debe comenzar y terminar el mismo día."
                )

        # ============================== VALIDAR FECHA ACTUAL ==============================

        ahora = timezone.now()

        if fecha_reserva:

            if fecha_reserva <= ahora:

                errores.append(
                    "La reserva debe comenzar en una fecha y hora futura."
                )

        # ============================== ANTICIPACIÓN MÁXIMA ==============================

        if (
            area
            and fecha_reserva
            and area.dias_anticipacion_maxima is not None
        ):

            dias_anticipacion = (
                fecha_reserva.date() - ahora.date()
            ).days

            if dias_anticipacion > area.dias_anticipacion_maxima:

                errores.append(
                    f"Esta área solo permite reservar con "
                    f"{area.dias_anticipacion_maxima} días de anticipación como máximo."
                )

        # ============================== DURACIÓN MÁXIMA ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and area.duracion_maxima is not None
        ):

            duracion = fin_reserva - fecha_reserva

            duracion_maxima = timedelta(
                hours=area.duracion_maxima
            )

            if duracion > duracion_maxima:

                errores.append(
                    f"La duración máxima permitida para esta área es de "
                    f"{area.duracion_maxima} horas."
                )

        # ============================== HORARIO DEL ÁREA ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and fecha_reserva.date() == fin_reserva.date()
        ):

            dia_semana = fecha_reserva.weekday()

            hora_inicio = fecha_reserva.time()
            hora_fin = fin_reserva.time()

            horario_valido = HorarioAreaComun.objects.filter(
                area_comun=area,
                dia_semana=dia_semana,
                hora_inicio__lte=hora_inicio,
                hora_fin__gte=hora_fin
            ).exists()

            if not horario_valido:

                errores.append(
                    "La reserva debe estar completamente dentro del horario configurado para el área común."
                )

        # ============================== RESERVAS QUE SE CRUZAN ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
        ):

            reservas_existentes = Reservacion.objects.filter(
                area_comun=area,
                estado__in=[
                    Reservacion.Estado.PENDIENTE,
                    Reservacion.Estado.APROBADO,
                ],
                fecha_reserva__lt=fin_reserva,
                fin_reserva__gt=fecha_reserva,
            )

            if reservas_existentes.exists():

                errores.append(
                    "El horario seleccionado se cruza con otra reserva existente para esta área."
                )

            # Cuando las reservas consecutivas están desactivadas,
            # tampoco se permite que una reserva termine exactamente
            # cuando comienza otra.
            elif not area.permite_reservas_consecutivas:

                reserva_anterior = Reservacion.objects.filter(
                    area_comun=area,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fin_reserva=fecha_reserva,
                ).exists()

                reserva_siguiente = Reservacion.objects.filter(
                    area_comun=area,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fecha_reserva=fin_reserva,
                ).exists()

                if reserva_anterior or reserva_siguiente:

                    errores.append(
                        "Esta área no permite reservas consecutivas."
                    )

        # ============================== CREAR RESERVA ==============================

        if not errores:

            reservacion = Reservacion(
                tipo_reserva=Reservacion.Tipo.AREA_COMUN,
                usuario=usuario_actual,
                unidad=unidad,
                fecha_reserva=fecha_reserva,
                fin_reserva=fin_reserva,
                estado=Reservacion.Estado.PENDIENTE,
                notas=notas,
                area_comun=area,
                nombre_invitado="",
            )

            try:
                reservacion.full_clean()

            except ValidationError as error:

                errores.extend(
                    error.messages
                )

            if not errores:

                reservacion.save()

                return redirect(
                    "inicio"
                )

        return render(
            request,
            "core/reservaciones/crear.html",
            {
                "usuario_actual": usuario_actual,
                "condominios": condominios,
                "areas": areas,
                "unidades": unidades,
                "errores": errores,
                "datos": request.POST,
            }
        )

    return render(
        request,
        "core/reservaciones/crear.html",
        {
            "usuario_actual": usuario_actual,
            "condominios": condominios,
            "areas": areas,
            "unidades": unidades,
        }
    )


# ============================== RESERVACIONES ==============================
@login_required
def reservacion_disponibilidad(request):
    """
    Devuelve la disponibilidad de un área común para una semana determinada.

    Incluye:
    - Horarios configurados para el área.
    - Reservas pendientes y aprobadas.
    - Información básica de las reglas del área.
    """

    usuario_actual = obtener_usuario(request)

    if not usuario_actual:
        return JsonResponse(
            {
                "error": "No se encontró el usuario actual."
            },
            status=403,
        )

    area_id = request.GET.get("area_id")
    fecha_parametro = request.GET.get("fecha")

    if not area_id:
        return JsonResponse(
            {
                "error": "Debes seleccionar un área común."
            },
            status=400,
        )

    try:
        area = AreaComun.objects.select_related(
            "condominio"
        ).get(
            id=area_id
        )
    except AreaComun.DoesNotExist:
        return JsonResponse(
            {
                "error": "El área común no existe."
            },
            status=404,
        )

    # =========================================================
    # VERIFICAR QUE EL USUARIO PERTENEZCA AL CONDOMINIO
    # =========================================================

    if not usuario_actual.puede_acceder_condominio(area.condominio_id):
        return JsonResponse(
            {
                "error": "No tienes acceso a esta área común."
            },
            status=403,
        )

    # =========================================================
    # FECHA DE REFERENCIA
    # =========================================================

    ahora = timezone.localtime()

    if fecha_parametro:
        try:
            fecha_referencia = datetime.strptime(
                fecha_parametro,
                "%Y-%m-%d"
            ).date()
        except ValueError:
            return JsonResponse(
                {
                    "error": "La fecha indicada no es válida."
                },
                status=400,
            )
    else:
        fecha_referencia = ahora.date()

    # =========================================================
    # OBTENER LUNES DE LA SEMANA
    # =========================================================

    lunes = (
        fecha_referencia
        - timedelta(
            days=fecha_referencia.weekday()
        )
    )

    domingo = lunes + timedelta(days=6)

    # =========================================================
    # HORARIOS CONFIGURADOS
    # =========================================================

    horarios = HorarioAreaComun.objects.filter(
        area_comun=area
    ).order_by(
        "dia_semana",
        "hora_inicio"
    )

    horarios_data = []

    for horario in horarios:

        horarios_data.append(
            {
                "id": horario.id,
                "dia_semana": horario.dia_semana,
                "hora_inicio": horario.hora_inicio.strftime(
                    "%H:%M"
                ),
                "hora_fin": horario.hora_fin.strftime(
                    "%H:%M"
                ),
            }
        )

    # =========================================================
    # RESERVAS EXISTENTES
    # =========================================================

    inicio_semana = timezone.make_aware(
        datetime.combine(
            lunes,
            time.min
        )
    )

    fin_semana = timezone.make_aware(
        datetime.combine(
            domingo + timedelta(days=1),
            time.min
        )
    )

    reservaciones = Reservacion.objects.filter(
        area_comun=area,
        tipo_reserva=Reservacion.Tipo.AREA_COMUN,
        estado__in=[
            Reservacion.Estado.PENDIENTE,
            Reservacion.Estado.APROBADO,
        ],
        fecha_reserva__lt=fin_semana,
        fin_reserva__gt=inicio_semana,
    ).select_related(
        "usuario",
        "unidad",
        "unidad__edificio",
    ).order_by(
        "fecha_reserva"
    )

    reservaciones_data = []

    for reservacion in reservaciones:

        reservaciones_data.append(
            {
                "id": reservacion.id,
                "inicio": timezone.localtime(
                    reservacion.fecha_reserva
                ).isoformat(),

                "fin": timezone.localtime(
                    reservacion.fin_reserva
                ).isoformat(),

                "estado": reservacion.estado,

                "usuario": (
                    reservacion.usuario.nombre
                    + " "
                    + reservacion.usuario.apellido
                ).strip(),
            }
        )

    # =========================================================
    # REGLAS DEL ÁREA
    # =========================================================

    reglas = {
        "duracion_maxima": area.duracion_maxima,
        "requiere_reserva": area.requiere_reserva,
        "permite_reservas_consecutivas": (
            area.permite_reservas_consecutivas
        ),
        "dias_anticipacion_maxima": (
            area.dias_anticipacion_maxima
        ),
        "capacidad": area.capacidad,
        "reservable": area.reservable,
    }

    # =============================================RESPUESTA============================

    return JsonResponse(
        {
            "area": {
                "id": area.id,
                "nombre": area.nombre,
            },

            "semana": {
                "lunes": lunes.isoformat(),
                "domingo": domingo.isoformat(),
            },

            "horarios": horarios_data,

            "reservaciones": reservaciones_data,

            "reglas": reglas,
        }
    )

@login_required
def reservaciones_lista(request):
    usuario_actual = obtener_usuario(request)

    es_super = es_super_administrador(usuario_actual)
    condominios = usuario_actual.condominios_asignados()
    if not condominios.exists():
        return redirect("inicio")

    # El personal consulta todas las reservas de su condominio; residentes
    # también pueden ver la agenda compartida, sin datos personales sensibles.
    reservaciones = Reservacion.objects.select_related(
        "usuario",
        "area_comun",
        "unidad",
        "unidad__edificio",
    )

    if not es_super:
        reservaciones = reservaciones.filter(
            unidad__edificio__condominio__in=condominios
        )

    filtro = request.GET.get("filtro", "todas")
    ahora = timezone.now()
    total_reservas = reservaciones.count()
    total_proximas = reservaciones.filter(
        fin_reserva__gte=ahora,
        estado__in=[Reservacion.Estado.PENDIENTE, Reservacion.Estado.APROBADO],
    ).count()
    total_historial = reservaciones.filter(
        Q(fin_reserva__lt=ahora)
        | Q(estado__in=[Reservacion.Estado.CANCELADO, Reservacion.Estado.RECHAZADO])
    ).count()
    if filtro == "proximas":
        reservaciones = reservaciones.filter(
            fin_reserva__gte=ahora,
            estado__in=[Reservacion.Estado.PENDIENTE, Reservacion.Estado.APROBADO],
        )
    elif filtro == "historial":
        reservaciones = reservaciones.filter(
            Q(fin_reserva__lt=ahora)
            | Q(estado__in=[Reservacion.Estado.CANCELADO, Reservacion.Estado.RECHAZADO])
        )
    elif filtro != "todas":
        filtro = "todas"

    reservaciones = list(reservaciones.order_by("-fecha_reserva", "-id"))
    puede_gestionar = es_super or tiene_rol(
        usuario_actual, "Administrador", "Gestor"
    )
    for reservacion in reservaciones:
        reservacion.puede_cancelar = (
            (puede_gestionar or reservacion.usuario_id == usuario_actual.id)
            and reservacion.estado in [Reservacion.Estado.PENDIENTE, Reservacion.Estado.APROBADO]
            and reservacion.fin_reserva is not None
            and reservacion.fin_reserva > ahora
        )
        reservacion.es_mia = reservacion.usuario_id == usuario_actual.id

    return render(
        request,
        "core/reservaciones/lista.html",
        {
            "usuario_actual": usuario_actual,
            "reservaciones": reservaciones,
            "filtro": filtro,
            "ahora": ahora,
            "total_reservas": total_reservas,
            "total_proximas": total_proximas,
            "total_historial": total_historial,
            "puede_gestionar_reservas": puede_gestionar,
        },
    )


# ============================== CREAR RESERVACIÓN ==============================

@login_required
def reservacion_crear(request):
    """
    Permite crear una reserva para un área común.

    Se validan:
    - Que el área permita reservas.
    - Que el usuario pertenezca al condominio.
    - Que exista una unidad válida.
    - Que la fecha no esté en el pasado.
    - Que se respete la anticipación máxima.
    - Que la duración máxima no sea superada.
    - Que la reserva esté completamente dentro
      del horario configurado para el área.
    - Que no exista otra reserva que se cruce.
    """

    usuario_actual = obtener_usuario(request)

    if not usuario_actual.condominios_asignados().exists():
        return redirect("inicio")

    condominios = usuario_actual.condominios_asignados()

    # ============================== ÁREAS DISPONIBLES ==============================

    areas = AreaComun.objects.filter(
        condominio__in=condominios,
        reservable=True
    ).prefetch_related(
        "horarios"
    ).order_by(
        "nombre"
    )

    # ============================== UNIDADES DISPONIBLES ==============================

    if es_residente(usuario_actual):

        unidades = Unidad.objects.filter(
            edificio__condominio__in=condominios
        ).filter(
            dueno=usuario_actual
        ) | Unidad.objects.filter(
            edificio__condominio__in=condominios,
            arrendatario=usuario_actual
        )

        unidades = unidades.select_related(
            "edificio"
        ).distinct().order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    else:

        unidades = Unidad.objects.filter(
            edificio__condominio__in=condominios
        ).select_related(
            "edificio"
        ).order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    if request.method == "POST":

        area_id = request.POST.get(
            "area_comun",
            ""
        ).strip()

        unidad_id = request.POST.get(
            "unidad",
            ""
        ).strip()

        fecha_reserva_texto = request.POST.get(
            "fecha_reserva",
            ""
        ).strip()

        fin_reserva_texto = request.POST.get(
            "fin_reserva",
            ""
        ).strip()

        notas = request.POST.get(
            "notas",
            ""
        ).strip()

        errores = []

        # ============================== ÁREA ==============================

        area = None

        if not area_id:

            errores.append(
                "Debes seleccionar un área común."
            )

        else:

            try:
                area = AreaComun.objects.get(
                    id=int(area_id),
                    condominio__in=condominios
                )

            except (
                AreaComun.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "El área común seleccionada no es válida."
                )

        if area and not area.reservable:

            errores.append(
                "El área común seleccionada no permite reservas."
            )

        # ============================== UNIDAD ==============================

        unidad = None

        if not unidad_id:

            errores.append(
                "Debes seleccionar una unidad."
            )

        else:

            try:
                unidad = Unidad.objects.select_related(
                    "edificio"
                ).get(
                    id=int(unidad_id),
                    edificio__condominio__in=condominios
                )

            except (
                Unidad.DoesNotExist,
                ValueError
            ):
                errores.append(
                    "La unidad seleccionada no es válida."
                )

        if (
            unidad
            and es_residente(usuario_actual)
            and unidad.dueno_id != usuario_actual.id
            and unidad.arrendatario_id != usuario_actual.id
        ):
            errores.append(
                "Solo puedes realizar reservas usando una unidad de la que seas propietario o arrendatario."
            )

        # ============================== FECHAS ==============================

        fecha_reserva = None
        fin_reserva = None

        if not fecha_reserva_texto:

            errores.append(
                "Debes indicar la fecha y hora de inicio."
            )

        else:

            try:
                fecha_reserva = datetime.fromisoformat(
                    fecha_reserva_texto
                )

                if timezone.is_naive(fecha_reserva):
                    fecha_reserva = timezone.make_aware(
                        fecha_reserva
                    )

            except ValueError:

                errores.append(
                    "La fecha y hora de inicio no son válidas."
                )

        if not fin_reserva_texto:

            errores.append(
                "Debes indicar la fecha y hora de término."
            )

        else:

            try:
                fin_reserva = datetime.fromisoformat(
                    fin_reserva_texto
                )

                if timezone.is_naive(fin_reserva):
                    fin_reserva = timezone.make_aware(
                        fin_reserva
                    )

            except ValueError:

                errores.append(
                    "La fecha y hora de término no son válidas."
                )

        # ============================== VALIDAR ORDEN ==============================

        if fecha_reserva and fin_reserva:

            if fin_reserva <= fecha_reserva:

                errores.append(
                    "La hora de término debe ser posterior a la hora de inicio."
                )

            if fecha_reserva.date() != fin_reserva.date():

                errores.append(
                    "La reserva debe comenzar y terminar el mismo día."
                )

        # ============================== VALIDAR FECHA ACTUAL ==============================

        ahora = timezone.now()

        if fecha_reserva:

            if fecha_reserva <= ahora:

                errores.append(
                    "La reserva debe comenzar en una fecha y hora futura."
                )

        # ============================== ANTICIPACIÓN MÁXIMA ==============================

        if (
            area
            and fecha_reserva
            and area.dias_anticipacion_maxima is not None
        ):

            dias_anticipacion = (
                fecha_reserva.date() - ahora.date()
            ).days

            if dias_anticipacion > area.dias_anticipacion_maxima:

                errores.append(
                    f"Esta área solo permite reservar con "
                    f"{area.dias_anticipacion_maxima} días de anticipación como máximo."
                )

        # ============================== DURACIÓN MÁXIMA ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and area.duracion_maxima is not None
        ):

            duracion = fin_reserva - fecha_reserva

            duracion_maxima = timedelta(
                hours=area.duracion_maxima
            )

            if duracion > duracion_maxima:

                errores.append(
                    f"La duración máxima permitida para esta área es de "
                    f"{area.duracion_maxima} horas."
                )

        # ============================== HORARIO DEL ÁREA ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and fecha_reserva.date() == fin_reserva.date()
        ):

            dia_semana = fecha_reserva.weekday()

            hora_inicio = fecha_reserva.time()
            hora_fin = fin_reserva.time()

            horario_valido = HorarioAreaComun.objects.filter(
                area_comun=area,
                dia_semana=dia_semana,
                hora_inicio__lte=hora_inicio,
                hora_fin__gte=hora_fin
            ).exists()

            if not horario_valido:

                errores.append(
                    "La reserva debe estar completamente dentro del horario configurado para el área común."
                )

        # ============================== RESERVAS QUE SE CRUZAN ==============================

        if (
            area
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
        ):

            reservas_existentes = Reservacion.objects.filter(
                area_comun=area,
                estado__in=[
                    Reservacion.Estado.PENDIENTE,
                    Reservacion.Estado.APROBADO,
                ],
                fecha_reserva__lt=fin_reserva,
                fin_reserva__gt=fecha_reserva,
            )

            if reservas_existentes.exists():

                errores.append(
                    "El horario seleccionado se cruza con otra reserva existente para esta área."
                )

            elif not area.permite_reservas_consecutivas:

                reserva_anterior = Reservacion.objects.filter(
                    area_comun=area,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fin_reserva=fecha_reserva,
                ).exists()

                reserva_siguiente = Reservacion.objects.filter(
                    area_comun=area,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fecha_reserva=fin_reserva,
                ).exists()

                if reserva_anterior or reserva_siguiente:

                    errores.append(
                        "Esta área no permite reservas consecutivas."
                    )

        # ============================== CREAR RESERVA ==============================

        if not errores:

            reservacion = Reservacion(
                tipo_reserva=Reservacion.Tipo.AREA_COMUN,
                usuario=usuario_actual,
                unidad=unidad,
                fecha_reserva=fecha_reserva,
                fin_reserva=fin_reserva,
                estado=Reservacion.Estado.PENDIENTE,
                notas=notas,
                area_comun=area,
                nombre_invitado="",
            )

            try:
                reservacion.full_clean()

            except ValidationError as error:

                errores.extend(
                    error.messages
                )

            if not errores:

                reservacion.save()

                return redirect(
                    "reservaciones_lista"
                )

        return render(
            request,
            "core/reservaciones/crear.html",
            {
                "usuario_actual": usuario_actual,
                "condominios": condominios,
                "areas": areas,
                "unidades": unidades,
                "errores": errores,
                "datos": request.POST,
            }
        )

    return render(
        request,
        "core/reservaciones/crear.html",
        {
            "usuario_actual": usuario_actual,
            "condominios": condominios,
            "areas": areas,
            "unidades": unidades,
        }
    )


# ============================== EDITAR RESERVACIÓN ==============================

@login_required
def reservacion_editar(request, reservacion_id):

    usuario_actual = obtener_usuario(request)

    reservacion = get_object_or_404(
        Reservacion.objects.select_related(
            "area_comun",
            "unidad",
            "unidad__edificio",
        ),
        id=reservacion_id
    )

    # Un residente solamente puede editar
    # sus propias reservas.
    if es_residente(usuario_actual):

        if reservacion.usuario_id != usuario_actual.id:
            return redirect("reservaciones_lista")

    # Por ahora solo permitimos editar reservas
    # de áreas comunes.
    if reservacion.tipo_reserva != Reservacion.Tipo.AREA_COMUN:
        return redirect("reservaciones_lista")

    if reservacion.estado == Reservacion.Estado.CANCELADO:
        return redirect("reservaciones_lista")

    if not reservacion.area_comun:
        return redirect("reservaciones_lista")

    area = reservacion.area_comun
    condominio = area.condominio

    if not usuario_actual.puede_acceder_condominio(condominio.id):
        return redirect("inicio")

    areas = AreaComun.objects.filter(
        condominio=condominio,
        reservable=True
    ).prefetch_related(
        "horarios"
    ).order_by(
        "nombre"
    )

    if es_residente(usuario_actual):

        unidades = Unidad.objects.filter(
            edificio__condominio=condominio
        ).filter(
            dueno=usuario_actual
        ) | Unidad.objects.filter(
            edificio__condominio=condominio,
            arrendatario=usuario_actual
        )

        unidades = unidades.select_related(
            "edificio"
        ).distinct().order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    else:

        unidades = Unidad.objects.filter(
            edificio__condominio=condominio
        ).select_related(
            "edificio"
        ).order_by(
            "edificio__nombre",
            "piso",
            "posicion"
        )

    if request.method == "POST":

        area_id = request.POST.get(
            "area_comun",
            ""
        ).strip()

        unidad_id = request.POST.get(
            "unidad",
            ""
        ).strip()

        fecha_reserva_texto = request.POST.get(
            "fecha_reserva",
            ""
        ).strip()

        fin_reserva_texto = request.POST.get(
            "fin_reserva",
            ""
        ).strip()

        notas = request.POST.get(
            "notas",
            ""
        ).strip()

        errores = []

        # ============================== ÁREA ==============================

        area_nueva = None

        try:

            area_nueva = AreaComun.objects.get(
                id=int(area_id),
                condominio=condominio,
                reservable=True
            )

        except (
            AreaComun.DoesNotExist,
            ValueError
        ):

            errores.append(
                "El área común seleccionada no es válida."
            )

        # ============================== UNIDAD ==============================

        unidad = None

        try:

            unidad = Unidad.objects.select_related(
                "edificio"
            ).get(
                id=int(unidad_id),
                edificio__condominio=condominio
            )

        except (
            Unidad.DoesNotExist,
            ValueError
        ):

            errores.append(
                "La unidad seleccionada no es válida."
            )

        if (
            unidad
            and es_residente(usuario_actual)
            and unidad.dueno_id != usuario_actual.id
            and unidad.arrendatario_id != usuario_actual.id
        ):

            errores.append(
                "Solo puedes realizar reservas usando una unidad de la que seas propietario o arrendatario."
            )

        # ============================== FECHAS ==============================

        fecha_reserva = None
        fin_reserva = None

        try:

            fecha_reserva = datetime.fromisoformat(
                fecha_reserva_texto
            )

            if timezone.is_naive(fecha_reserva):
                fecha_reserva = timezone.make_aware(
                    fecha_reserva
                )

        except ValueError:

            errores.append(
                "La fecha y hora de inicio no son válidas."
            )

        try:

            fin_reserva = datetime.fromisoformat(
                fin_reserva_texto
            )

            if timezone.is_naive(fin_reserva):
                fin_reserva = timezone.make_aware(
                    fin_reserva
                )

        except ValueError:

            errores.append(
                "La fecha y hora de término no son válidas."
            )

        # ============================== VALIDACIONES ==============================

        if fecha_reserva and fin_reserva:

            if fin_reserva <= fecha_reserva:

                errores.append(
                    "La hora de término debe ser posterior a la hora de inicio."
                )

            if fecha_reserva.date() != fin_reserva.date():

                errores.append(
                    "La reserva debe comenzar y terminar el mismo día."
                )

        ahora = timezone.now()

        if fecha_reserva and fecha_reserva <= ahora:

            errores.append(
                "La reserva debe comenzar en una fecha y hora futura."
            )

        if (
            area_nueva
            and fecha_reserva
            and area_nueva.dias_anticipacion_maxima is not None
        ):

            dias_anticipacion = (
                fecha_reserva.date() - ahora.date()
            ).days

            if dias_anticipacion > area_nueva.dias_anticipacion_maxima:

                errores.append(
                    f"Esta área solo permite reservar con "
                    f"{area_nueva.dias_anticipacion_maxima} días de anticipación como máximo."
                )

        if (
            area_nueva
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and area_nueva.duracion_maxima is not None
        ):

            duracion = fin_reserva - fecha_reserva

            if duracion > timedelta(
                hours=area_nueva.duracion_maxima
            ):

                errores.append(
                    f"La duración máxima permitida para esta área es de "
                    f"{area_nueva.duracion_maxima} horas."
                )

        # ============================== HORARIO ==============================

        if (
            area_nueva
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
            and fecha_reserva.date() == fin_reserva.date()
        ):

            dia_semana = fecha_reserva.weekday()

            horario_valido = HorarioAreaComun.objects.filter(
                area_comun=area_nueva,
                dia_semana=dia_semana,
                hora_inicio__lte=fecha_reserva.time(),
                hora_fin__gte=fin_reserva.time(),
            ).exists()

            if not horario_valido:

                errores.append(
                    "La reserva debe estar completamente dentro del horario configurado para el área común."
                )

        # ============================== CRUCES ==============================

        if (
            area_nueva
            and fecha_reserva
            and fin_reserva
            and fin_reserva > fecha_reserva
        ):

            reservas_existentes = Reservacion.objects.filter(
                area_comun=area_nueva,
                estado__in=[
                    Reservacion.Estado.PENDIENTE,
                    Reservacion.Estado.APROBADO,
                ],
                fecha_reserva__lt=fin_reserva,
                fin_reserva__gt=fecha_reserva,
            ).exclude(
                id=reservacion.id
            )

            if reservas_existentes.exists():

                errores.append(
                    "El horario seleccionado se cruza con otra reserva existente para esta área."
                )

            elif not area_nueva.permite_reservas_consecutivas:

                reserva_anterior = Reservacion.objects.filter(
                    area_comun=area_nueva,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fin_reserva=fecha_reserva,
                ).exclude(
                    id=reservacion.id
                ).exists()

                reserva_siguiente = Reservacion.objects.filter(
                    area_comun=area_nueva,
                    estado__in=[
                        Reservacion.Estado.PENDIENTE,
                        Reservacion.Estado.APROBADO,
                    ],
                    fecha_reserva=fin_reserva,
                ).exclude(
                    id=reservacion.id
                ).exists()

                if reserva_anterior or reserva_siguiente:

                    errores.append(
                        "Esta área no permite reservas consecutivas."
                    )

        # ============================== GUARDAR ==============================

        if not errores:

            reservacion.area_comun = area_nueva
            reservacion.unidad = unidad
            reservacion.fecha_reserva = fecha_reserva
            reservacion.fin_reserva = fin_reserva
            reservacion.notas = notas

            try:

                reservacion.full_clean()

            except ValidationError as error:

                errores.extend(
                    error.messages
                )

            if not errores:

                reservacion.save()

                return redirect(
                    "reservaciones_lista"
                )

        return render(
            request,
            "core/reservaciones/editar.html",
            {
                "usuario_actual": usuario_actual,
                "reservacion": reservacion,
                "areas": areas,
                "unidades": unidades,
                "errores": errores,
                "datos": request.POST,
            }
        )

    return render(
        request,
        "core/reservaciones/editar.html",
        {
            "usuario_actual": usuario_actual,
            "reservacion": reservacion,
            "areas": areas,
            "unidades": unidades,
        }
    )

# ============================== CANCELAR RESERVACIÓN ==============================

@login_required
def reservacion_cancelar(request, reservacion_id):
    if request.method != "POST":
        return redirect("reservaciones_lista")

    usuario_actual = obtener_usuario(request)
    reservacion = get_object_or_404(
        Reservacion.objects.select_related("unidad__edificio"),
        id=reservacion_id
    )
    es_super = es_super_administrador(usuario_actual)
    es_personal = es_super or tiene_rol(usuario_actual, "Administrador", "Gestor")

    if not usuario_actual.puede_acceder_condominio(reservacion.unidad.edificio.condominio_id):
        messages.error(request, "No tienes permiso para modificar esta reserva.")
        return redirect("reservaciones_lista")

    if not es_personal and reservacion.usuario_id != usuario_actual.id:
        messages.error(request, "Solo puedes cancelar tus propias reservas.")
        return redirect("reservaciones_lista")

    if reservacion.estado not in [Reservacion.Estado.PENDIENTE, Reservacion.Estado.APROBADO]:
        messages.info(request, "Esta reserva ya no está activa.")
    elif reservacion.fin_reserva and reservacion.fin_reserva <= timezone.now():
        messages.info(request, "Las reservas pasadas se conservan en el historial.")
    else:
        # Se conserva el registro para que siga apareciendo en el historial.
        reservacion.estado = Reservacion.Estado.CANCELADO
        reservacion.save(update_fields=["estado"])
        messages.success(request, "La reserva se canceló y quedó en el historial.")

    return redirect(
        "reservaciones_lista"
    )


# ============================== PÁGINA PÚBLICA ==============================

def landing(request):
    if setup_disponible():
        return redirect("launch:launch")

    if request.user.is_authenticated:
        return redirect("inicio")

    return render(
        request,
        "core/landing.html"
    )

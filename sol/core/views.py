from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import PasswordResetView, PasswordResetDoneView, PasswordResetConfirmView, PasswordResetCompleteView
from django.urls import reverse_lazy
from django.db import transaction

from .models import Usuario, Rol, Condominio


#============================== FUNCIONES AUXILIARES ==============================

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


#============================== AUTENTICACIÓN ==============================

def login_view(request):
    if request.user.is_authenticated:
        return redirect("inicio")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")

        usuario_django = authenticate(request, username=username, password=password)

        if usuario_django is not None:
            try:
                usuario = Usuario.objects.select_related("rol", "condominio").get(user=usuario_django)
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
    return redirect("login")


#============================== HU-010: RECUPERAR CONTRASEÑA ==============================

def password_reset_request(request):
    # Sincroniza los correos del perfil con el usuario de Django.
    # Esto permite que Django encuentre correctamente la cuenta.
    for usuario in Usuario.objects.select_related("user").filter(user__isnull=False):
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
    print("UID:", uidb64)
    print("TOKEN:", token)

    vista = PasswordResetConfirmView.as_view(
        template_name="core/password_reset_confirm.html",
        success_url=reverse_lazy("password_reset_complete")
    )

    return vista(request, uidb64=uidb64, token=token)


def password_reset_complete(request):
    vista = PasswordResetCompleteView.as_view(
        template_name="core/password_reset_complete.html"
    )

    return vista(request)


#============================== DASHBOARD ==============================

@login_required
def inicio(request):
    usuario = obtener_usuario(request)

    return render(request, "core/inicio.html", {
        "usuario": usuario,
    })


#============================== ADMINISTRACIÓN ==============================

@login_required
def administracion(request):
    usuario = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario):
        return redirect("inicio")

    if es_super_administrador(usuario):
        condominios = Condominio.objects.filter(activo=True)
        usuarios = Usuario.objects.select_related("user", "rol", "condominio").all()
    else:
        condominios = Condominio.objects.filter(
            id=usuario.condominio_id,
            activo=True
        )
        usuarios = Usuario.objects.select_related(
            "user", "rol", "condominio"
        ).filter(condominio=usuario.condominio)

    return render(request, "core/administracion.html", {
        "usuario": usuario,
        "condominios": condominios,
        "usuarios": usuarios,
    })


#============================== LISTA DE USUARIOS ==============================

@login_required
def usuarios_lista(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    if es_super_administrador(usuario_actual):
        usuarios = Usuario.objects.select_related(
            "user", "rol", "condominio"
        ).all()
    else:
        usuarios = Usuario.objects.select_related(
            "user", "rol", "condominio"
        ).filter(condominio=usuario_actual.condominio)

    return render(request, "core/usuarios/lista.html", {
        "usuario_actual": usuario_actual,
        "usuarios": usuarios,
    })


#============================== CREAR USUARIO ==============================

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
        password_confirmacion = request.POST.get("password_confirmacion", "")
        nombre = request.POST.get("nombre", "").strip()
        nombre2 = request.POST.get("nombre2", "").strip()
        apellido = request.POST.get("apellido", "").strip()
        apellido2 = request.POST.get("apellido2", "").strip()
        email = request.POST.get("email", "").strip()
        telefono = request.POST.get("telefono", "").strip()
        rut = request.POST.get("rut", "").strip()
        rol_id = request.POST.get("rol", "").strip()
        condominio_id = request.POST.get("condominio", "").strip()

        errores = []

        if not username:
            errores.append("Debes ingresar un nombre de usuario.")

        if not password:
            errores.append("Debes ingresar una contraseña.")

        if password != password_confirmacion:
            errores.append("Las contraseñas no coinciden.")

        if not nombre:
            errores.append("Debes ingresar el nombre.")

        if not apellido:
            errores.append("Debes ingresar el apellido.")

        if not email:
            errores.append("Debes ingresar un correo electrónico.")

        if not rol_id:
            errores.append("Debes seleccionar un rol.")

        if username and User.objects.filter(username=username).exists():
            errores.append("El nombre de usuario ya está registrado.")

        if email and Usuario.objects.filter(email=email).exists():
            errores.append("El correo electrónico ya está registrado.")

        rol = None

        if rol_id:
            try:
                rol = Rol.objects.get(id=int(rol_id))
            except (Rol.DoesNotExist, ValueError):
                errores.append("El rol seleccionado no es válido.")

        condominio = None

        if condominio_id:
            try:
                condominio = Condominio.objects.get(id=int(condominio_id))
            except (Condominio.DoesNotExist, ValueError):
                errores.append("El condominio seleccionado no es válido.")

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
                    errores.append("El condominio seleccionado no es válido.")

            condominio = usuario_actual.condominio

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
                    telefono=telefono,
                    rut=rut or None,
                    activo=True,
                )

            return redirect("usuarios_lista")

        return render(request, "core/usuarios/crear.html", {
            "usuario_actual": usuario_actual,
            "roles": roles,
            "condominios": condominios,
            "errores": errores,
            "datos": request.POST,
        })

    return render(request, "core/usuarios/crear.html", {
        "usuario_actual": usuario_actual,
        "roles": roles,
        "condominios": condominios,
    })


#============================== EDITAR USUARIO ==============================

@login_required
def usuario_editar(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related("user", "rol", "condominio"),
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
        email = request.POST.get("email", "").strip()
        telefono = request.POST.get("telefono", "").strip()
        rut = request.POST.get("rut", "").strip()
        rol_id = request.POST.get("rol", "").strip()
        condominio_id = request.POST.get("condominio", "").strip()
        activo = request.POST.get("activo") == "on"

        errores = []

        if not nombre:
            errores.append("Debes ingresar el nombre.")

        if not apellido:
            errores.append("Debes ingresar el apellido.")

        if not email:
            errores.append("Debes ingresar un correo.")

        if Usuario.objects.filter(email=email).exclude(id=usuario.id).exists():
            errores.append("El correo electrónico ya está registrado.")

        rol = None

        if not rol_id:
            errores.append("Debes seleccionar un rol.")
        else:
            try:
                rol = Rol.objects.get(id=int(rol_id))
            except (Rol.DoesNotExist, ValueError):
                errores.append("El rol seleccionado no es válido.")

        condominio = None

        if es_administrador(usuario_actual):
            condominio = usuario_actual.condominio

            if not condominio:
                errores.append("El Administrador no tiene un condominio asociado.")
        else:
            if condominio_id:
                try:
                    condominio = Condominio.objects.get(id=int(condominio_id))
                except (Condominio.DoesNotExist, ValueError):
                    errores.append("El condominio seleccionado no es válido.")
            else:
                condominio = None

        if es_administrador(usuario_actual):
            if rol and rol.nombre == "Super Administrador":
                errores.append(
                    "Un Administrador no puede asignar el rol de Super Administrador."
                )

        if not errores:
            with transaction.atomic():
                usuario.nombre = nombre
                usuario.nombre2 = nombre2
                usuario.apellido = apellido
                usuario.apellido2 = apellido2
                usuario.email = email
                usuario.telefono = telefono
                usuario.rut = rut or None
                usuario.rol = rol
                usuario.condominio = condominio
                usuario.activo = activo
                usuario.save()

                if usuario.user:
                    usuario.user.email = email
                    usuario.user.is_active = activo
                    usuario.user.save(update_fields=["email", "is_active"])

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


#============================== CAMBIAR ESTADO DE USUARIO ==============================

@login_required
def usuario_cambiar_estado(request, usuario_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_usuarios(usuario_actual):
        return redirect("inicio")

    usuario = get_object_or_404(
        Usuario.objects.select_related("rol", "condominio", "user"),
        id=usuario_id
    )

    if es_administrador(usuario_actual):
        if usuario.condominio_id != usuario_actual.condominio_id:
            return redirect("usuarios_lista")

        if es_super_administrador(usuario):
            return redirect("usuarios_lista")

    usuario.activo = not usuario.activo
    usuario.save(update_fields=["activo"])

    if usuario.user:
        usuario.user.is_active = usuario.activo
        usuario.user.save(update_fields=["is_active"])

    return redirect("usuarios_lista")


#============================== RF-01: CONDOMINIOS ==============================

@login_required
def condominios_lista(request):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    if es_super_administrador(usuario_actual):
        condominios = Condominio.objects.filter(activo=True)
    else:
        condominios = Condominio.objects.filter(
            id=usuario_actual.condominio_id,
            activo=True
        )

    return render(request, "core/condominios/lista.html", {
        "usuario_actual": usuario_actual,
        "condominios": condominios,
    })


#============================== CREAR CONDOMINIO ==============================

@login_required
def condominio_crear(request):
    usuario_actual = obtener_usuario(request)

    if not es_super_administrador(usuario_actual):
        return redirect("condominios_lista")

    from .models import Ciudad

    ciudades = Ciudad.objects.all()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_id = request.POST.get("ciudad", "").strip()
        edificios = request.POST.get("edificios", "1").strip()

        errores = []

        if not nombre:
            errores.append("Debes ingresar el nombre del condominio.")

        if not direccion:
            errores.append("Debes ingresar la dirección.")

        if not ciudad_id:
            errores.append("Debes seleccionar una ciudad.")

        try:
            edificios_numero = int(edificios)

            if edificios_numero < 1:
                errores.append(
                    "El condominio debe tener al menos un edificio."
                )
        except ValueError:
            edificios_numero = 1
            errores.append("La cantidad de edificios no es válida.")

        ciudad = None

        if ciudad_id:
            try:
                ciudad = Ciudad.objects.get(id=int(ciudad_id))
            except (Ciudad.DoesNotExist, ValueError):
                errores.append("La ciudad seleccionada no es válida.")

        if not errores:
            Condominio.objects.create(
                nombre=nombre,
                direccion=direccion,
                ciudad=ciudad,
                edificios=edificios_numero,
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


#============================== EDITAR CONDOMINIO ==============================

@login_required
def condominio_editar(request, condominio_id):
    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(Condominio, id=condominio_id)

    if es_administrador(usuario_actual):
        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    from .models import Ciudad

    ciudades = Ciudad.objects.all()

    if request.method == "POST":
        nombre = request.POST.get("nombre", "").strip()
        direccion = request.POST.get("direccion", "").strip()
        ciudad_id = request.POST.get("ciudad", "").strip()
        activo = request.POST.get("activo") == "on"

        errores = []

        if not nombre:
            errores.append("Debes ingresar el nombre del condominio.")

        if not direccion:
            errores.append("Debes ingresar la dirección.")

        ciudad = None

        if ciudad_id:
            try:
                ciudad = Ciudad.objects.get(id=int(ciudad_id))
            except (Ciudad.DoesNotExist, ValueError):
                errores.append("La ciudad seleccionada no es válida.")
        else:
            errores.append("Debes seleccionar una ciudad.")

        if not errores:
            condominio.nombre = nombre
            condominio.direccion = direccion
            condominio.ciudad = ciudad
            condominio.activo = activo
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


#============================== EDIFICIOS DE UN CONDOMINIO ==============================

@login_required
def edificios_lista(request, condominio_id):
    from .models import Edificio

    usuario_actual = obtener_usuario(request)

    if not puede_gestionar_condominio(usuario_actual):
        return redirect("inicio")

    condominio = get_object_or_404(Condominio, id=condominio_id)

    if es_administrador(usuario_actual):
        if condominio.id != usuario_actual.condominio_id:
            return redirect("condominios_lista")

    edificios = condominio.edificios_set.all().prefetch_related("unidades")

    return render(request, "core/condominios/edificios.html", {
        "usuario_actual": usuario_actual,
        "condominio": condominio,
        "edificios": edificios,
    })


#============================== EDITAR EDIFICIO ==============================

@login_required
def edificio_editar(request, edificio_id):
    from .models import Edificio

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
        numero_pisos = request.POST.get("numero_pisos", "").strip()
        viviendas_por_piso = request.POST.get("viviendas_por_piso", "").strip()
        subterraneo = request.POST.get("subterraneo") == "on"

        errores = []

        if not nombre:
            errores.append("Debes ingresar el nombre del edificio.")

        try:
            pisos = int(numero_pisos)

            if pisos < 2 or pisos > 20:
                errores.append(
                    "El número de pisos debe estar entre 2 y 20."
                )
        except ValueError:
            pisos = None
            errores.append("El número de pisos no es válido.")

        try:
            viviendas = int(viviendas_por_piso)

            if viviendas < 1:
                errores.append(
                    "Debe existir al menos una vivienda por piso."
                )
        except ValueError:
            viviendas = None
            errores.append("La cantidad de viviendas no es válida.")

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

        return render(request, "core/condominios/editar.html", {
            "usuario_actual": usuario_actual,
            "edificio": edificio,
            "condominio": condominio,
            "errores": errores,
        })

    return render(request, "core/condominios/editar.html", {
        "usuario_actual": usuario_actual,
        "edificio": edificio,
        "condominio": condominio,
    })


#============================== UNIDADES DE UN EDIFICIO ==============================

@login_required
def unidades_lista(request, edificio_id):
    from .models import Edificio

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
    ).order_by("piso", "posicion")

    return render(request, "core/condominios/unidades.html", {
        "usuario_actual": usuario_actual,
        "edificio": edificio,
        "condominio": edificio.condominio,
        "unidades": unidades,
    })


#============================== EDITAR UNIDAD ==============================

@login_required
def unidad_editar(request, unidad_id):
    from .models import Unidad

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
    ).select_related("rol").order_by("apellido", "nombre")

    if request.method == "POST":
        dueno_id = request.POST.get("dueno_id", "").strip()
        arrendatario_id = request.POST.get("arrendatario_id", "").strip()

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
            except (Usuario.DoesNotExist, ValueError):
                errores.append("El propietario seleccionado no es válido.")

        if arrendatario_id:
            try:
                arrendatario = Usuario.objects.get(
                    id=int(arrendatario_id),
                    condominio=condominio,
                    activo=True,
                    rol__nombre="Residente"
                )
            except (Usuario.DoesNotExist, ValueError):
                errores.append("El arrendatario seleccionado no es válido.")

        if not errores:
            unidad.dueno = dueno
            unidad.arrendatario = arrendatario
            unidad.save()

            return redirect(
                "unidades_lista",
                edificio_id=unidad.edificio.id
            )

        return render(request, "core/condominios/unidad_editar.html", {
            "usuario_actual": usuario_actual,
            "unidad": unidad,
            "usuarios": usuarios,
            "errores": errores,
        })

    return render(request, "core/condominios/unidad_editar.html", {
        "usuario_actual": usuario_actual,
        "unidad": unidad,
        "usuarios": usuarios,
    })
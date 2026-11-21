"""
Vistas de cuenta propia y del panel de dueños/arrendatarios:

- Mis datos: cada usuario edita sus datos personales, foto de perfil y contraseña.
- Mi unidad: el dueño o el arrendatario ve su unidad, actualiza cuántas personas viven
  y el dueño puede solicitar la baja de su arrendatario.
- Solicitudes de baja: el Administrador aprueba o rechaza esas solicitudes.
"""
import mimetypes

from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from anuncios.models import validar_imagen
from notis.signals import notificar

from .models import SolicitudBajaArrendatario, Unidad, Usuario
from .validators import formatear_telefono, validar_email, validar_telefono


ROLES_STAFF = (Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR)
# Las bajas las ven y resuelven el Administrador y el Gestor de cada condominio (el Super Administrador no).
ROLES_RESUELVEN_BAJAS = (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR)
MAX_RESIDENTES = 20


def _perfil(request):
    try:
        return request.user.usuario
    except (AttributeError, ObjectDoesNotExist):
        return None


# ============================== MIS DATOS ==============================

def _validar_datos_personales(post, perfil):
    datos = {
        "nombre": post.get("nombre", "").strip(),
        "nombre2": post.get("nombre2", "").strip(),
        "apellido": post.get("apellido", "").strip(),
        "apellido2": post.get("apellido2", "").strip(),
        "email": post.get("email", "").strip().lower(),
        "telefono": post.get("telefono", "").strip(),
    }
    errores = []

    if not datos["nombre"]:
        errores.append("El primer nombre es obligatorio.")
    if not datos["apellido"]:
        errores.append("El primer apellido es obligatorio.")
    for campo, etiqueta in (("nombre", "primer nombre"), ("nombre2", "segundo nombre"),
                            ("apellido", "primer apellido"), ("apellido2", "segundo apellido")):
        if len(datos[campo]) > 30:
            errores.append(f"El {etiqueta} puede tener como máximo 30 caracteres.")

    try:
        validar_email(datos["email"])
        if len(datos["email"]) > 50:
            errores.append("El correo electrónico puede tener como máximo 50 caracteres.")
        elif Usuario.objects.filter(email__iexact=datos["email"]).exclude(pk=perfil.pk).exists():
            errores.append("Ese correo electrónico ya está registrado por otro usuario.")
    except ValidationError as error:
        errores.extend(error.messages)

    telefono_normalizado = ""
    if datos["telefono"]:
        try:
            validar_telefono(datos["telefono"])
            telefono_normalizado = formatear_telefono(datos["telefono"])
            if len(telefono_normalizado) > 15:
                errores.append("El teléfono es demasiado largo.")
        except ValidationError as error:
            errores.extend(error.messages)

    return datos, telefono_normalizado, errores


@login_required
def mis_datos(request):
    perfil = _perfil(request)
    if perfil is None:
        return redirect("inicio")

    errores = []
    datos = None
    form_password = PasswordChangeForm(request.user)
    seccion_activa = "datos"

    if request.method == "POST":
        accion = request.POST.get("accion", "datos")

        if accion == "quitar_foto":
            if perfil.avatar:
                perfil.avatar.delete(save=False)
                perfil.avatar = None
                perfil.save()
                messages.success(request, "Quitamos tu foto de perfil.")
            return redirect("mis_datos")

        if accion == "password":
            seccion_activa = "password"
            form_password = PasswordChangeForm(request.user, request.POST)
            if form_password.is_valid():
                user = form_password.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Tu contraseña se actualizó.")
                return redirect("mis_datos")

        else:
            datos, telefono, errores = _validar_datos_personales(request.POST, perfil)
            foto = request.FILES.get("avatar")
            if foto:
                try:
                    validar_imagen(foto)
                except ValidationError as error:
                    errores.extend(error.messages)

            if not errores:
                foto_anterior = perfil.avatar.name if perfil.avatar else ""
                with transaction.atomic():
                    perfil.nombre = datos["nombre"]
                    perfil.nombre2 = datos["nombre2"]
                    perfil.apellido = datos["apellido"]
                    perfil.apellido2 = datos["apellido2"]
                    perfil.email = datos["email"]
                    perfil.telefono = telefono
                    if foto:
                        perfil.avatar = foto
                    perfil.save()
                    if perfil.user_id and request.user.email != datos["email"]:
                        request.user.email = datos["email"]
                        request.user.save(update_fields=["email"])

                # La foto anterior ya no la usa nadie: se borra del disco
                if foto and foto_anterior and foto_anterior != perfil.avatar.name:
                    perfil.avatar.storage.delete(foto_anterior)

                messages.success(request, "Tus datos se guardaron.")
                return redirect("mis_datos")

    return render(request, "core/cuenta/mis_datos.html", {
        "usuario_perfil": perfil,
        "datos": datos,
        "errores": errores,
        "form_password": form_password,
        "seccion_activa": seccion_activa,
    })


@login_required
def avatar_usuario(request, usuario_id):
    """Sirve la foto de perfil solo a quien comparte condominio con el usuario (o al staff que lo gestiona)."""
    perfil = _perfil(request)
    usuario = get_object_or_404(Usuario, pk=usuario_id)
    if perfil is None or not usuario.avatar:
        raise Http404

    permitido = (
        usuario.pk == perfil.pk
        or perfil.rol == Usuario.Rol.SUPER
        or (usuario.condominio_id and perfil.puede_acceder_condominio(usuario.condominio_id))
    )
    if not permitido:
        raise Http404

    try:
        archivo = usuario.avatar.open("rb")
    except FileNotFoundError:
        raise Http404
    tipo = mimetypes.guess_type(usuario.avatar.name)[0] or "image/jpeg"
    respuesta = FileResponse(archivo, content_type=tipo)
    respuesta["Cache-Control"] = "private, max-age=300"
    return respuesta


# ============================== MI UNIDAD ==============================

def _unidades_de(perfil):
    return (
        Unidad.objects.filter(Q(dueno=perfil) | Q(arrendatario=perfil))
        .select_related("edificio__condominio", "dueno", "arrendatario")
        .distinct()
        .order_by("edificio__condominio__nombre", "edificio__numero", "numero_unidad")
    )


def puede_editar_residentes(unidad, perfil):
    """Con arrendatario, la cantidad de residentes la actualiza él; sin arrendatario, el dueño."""
    if unidad.arrendatario_id:
        return unidad.arrendatario_id == perfil.pk
    return unidad.dueno_id == perfil.pk


@login_required
def mi_unidad(request):
    perfil = _perfil(request)
    if perfil is None or perfil.rol != Usuario.Rol.RESIDENTE:
        return redirect("inicio")

    unidades = list(_unidades_de(perfil))
    errores = []

    if request.method == "POST":
        accion = request.POST.get("accion", "")
        unidad = next((u for u in unidades if str(u.pk) == request.POST.get("unidad", "")), None)

        if unidad is None:
            errores.append("La unidad seleccionada no es válida.")

        elif accion == "residentes":
            if not puede_editar_residentes(unidad, perfil):
                errores.append(
                    "Esta unidad tiene arrendatario: la cantidad de residentes la actualiza él."
                    if unidad.dueno_id == perfil.pk else
                    "No puedes modificar los datos de esta unidad."
                )
            else:
                try:
                    cantidad = int(request.POST.get("cantidad_residentes", ""))
                except ValueError:
                    cantidad = 0
                if not 1 <= cantidad <= MAX_RESIDENTES:
                    errores.append(f"Indica entre 1 y {MAX_RESIDENTES} residentes.")
                else:
                    unidad.cantidad_residentes = cantidad
                    unidad.save(update_fields=["cantidad_residentes"])
                    messages.success(request, f"Actualizamos los residentes de la unidad {unidad.numero_unidad}.")
                    return redirect("mi_unidad")

        elif accion == "solicitar_baja":
            motivo = request.POST.get("motivo", "").strip()
            if unidad.dueno_id != perfil.pk:
                errores.append("Solo el dueño puede solicitar la baja del arrendatario.")
            elif not unidad.arrendatario_id:
                errores.append("Esta unidad no tiene arrendatario.")
            elif not motivo:
                errores.append("Cuéntale al administrador el motivo de la baja.")
            elif len(motivo) > 1000:
                errores.append("El motivo puede tener como máximo 1000 caracteres.")
            elif unidad.solicitudes_baja.filter(estado=SolicitudBajaArrendatario.Estado.PENDIENTE).exists():
                errores.append("Ya hay una solicitud de baja pendiente para esta unidad.")
            else:
                with transaction.atomic():
                    solicitud = SolicitudBajaArrendatario.objects.create(
                        unidad=unidad,
                        arrendatario=unidad.arrendatario,
                        solicitante=perfil,
                        motivo=motivo,
                    )
                    notificar(
                        resuelven_bajas_del_condominio(unidad.edificio.condominio_id),
                        f"{perfil} solicitó dar de baja al arrendatario de la unidad {unidad.numero_unidad} "
                        f"({unidad.edificio.nombre}).",
                        remitente=perfil,
                        url=reverse("solicitudes_baja_lista"),
                    )
                    notificar(
                        [solicitud.arrendatario],
                        f"El dueño de la unidad {unidad.numero_unidad} solicitó terminar tu arriendo en la plataforma. "
                        "El administrador se comunicará contigo antes de resolverlo.",
                        remitente=perfil,
                        url=reverse("mi_unidad"),
                    )
                messages.success(request, "Enviamos la solicitud al administrador. Te avisaremos cuando la resuelva.")
                return redirect("mi_unidad")

        elif accion == "cancelar_baja":
            solicitud = unidad.solicitudes_baja.filter(
                estado=SolicitudBajaArrendatario.Estado.PENDIENTE, solicitante=perfil,
            ).first()
            if solicitud is None:
                errores.append("No hay una solicitud pendiente que cancelar.")
            else:
                solicitud.estado = SolicitudBajaArrendatario.Estado.CANCELADA
                solicitud.resuelto_en = timezone.now()
                solicitud.save(update_fields=["estado", "resuelto_en"])
                notificar(
                    [solicitud.arrendatario],
                    f"El dueño de la unidad {unidad.numero_unidad} canceló la solicitud de baja de tu arriendo.",
                    remitente=perfil,
                )
                messages.success(request, "Cancelaste la solicitud de baja.")
                return redirect("mi_unidad")

        else:
            errores.append("Acción no válida.")

    pendientes = {
        s.unidad_id: s
        for s in SolicitudBajaArrendatario.objects.filter(
            unidad__in=unidades, estado=SolicitudBajaArrendatario.Estado.PENDIENTE,
        ).select_related("solicitante")
    }
    for unidad in unidades:
        unidad.soy_dueno = unidad.dueno_id == perfil.pk
        unidad.soy_arrendatario = unidad.arrendatario_id == perfil.pk
        unidad.puedo_editar_residentes = puede_editar_residentes(unidad, perfil)
        unidad.baja_pendiente = pendientes.get(unidad.pk)

    return render(request, "core/cuenta/mi_unidad.html", {
        "unidades": unidades,
        "errores": errores,
        "max_residentes": MAX_RESIDENTES,
    })


# ============================== SOLICITUDES DE BAJA (ADMIN) ==============================

def resuelven_bajas_del_condominio(condominio_id):
    """Administradores y Gestores activos del condominio (incluye a los Administradores que lo tienen asignado)."""
    return Usuario.objects.filter(
        Q(condominio_id=condominio_id, rol__in=ROLES_RESUELVEN_BAJAS)
        | Q(condominios_administrados=condominio_id, rol=Usuario.Rol.ADMINISTRADOR),
        activo=True, user__isnull=False,
    ).distinct()


def _solicitudes_visibles(perfil):
    qs = SolicitudBajaArrendatario.objects.select_related(
        "unidad__edificio__condominio", "arrendatario", "solicitante", "resuelto_por",
    )
    if perfil.rol not in ROLES_RESUELVEN_BAJAS:
        return qs.none()
    return qs.filter(unidad__edificio__condominio__in=perfil.condominios_asignados())


@login_required
def solicitudes_baja_lista(request):
    perfil = _perfil(request)
    if perfil is None or perfil.rol not in ROLES_RESUELVEN_BAJAS:
        return redirect("inicio")

    estado = request.GET.get("estado", SolicitudBajaArrendatario.Estado.PENDIENTE)
    if estado not in SolicitudBajaArrendatario.Estado.values and estado != "todas":
        estado = SolicitudBajaArrendatario.Estado.PENDIENTE

    solicitudes = _solicitudes_visibles(perfil)
    if estado != "todas":
        solicitudes = solicitudes.filter(estado=estado)

    return render(request, "core/cuenta/solicitudes_baja.html", {
        "solicitudes": solicitudes,
        "estado": estado,
        "estados": SolicitudBajaArrendatario.Estado.choices,
        "total_pendientes": _solicitudes_visibles(perfil).filter(
            estado=SolicitudBajaArrendatario.Estado.PENDIENTE
        ).count(),
    })


def _desactivar_si_no_tiene_otras_unidades(arrendatario):
    """Desactiva la cuenta del ex arrendatario si ya no está ligado a ninguna unidad."""
    if Unidad.objects.filter(Q(dueno=arrendatario) | Q(arrendatario=arrendatario)).exists():
        return False
    arrendatario.activo = False
    arrendatario.save(update_fields=["activo"])
    if arrendatario.user_id:
        arrendatario.user.is_active = False
        arrendatario.user.save(update_fields=["is_active"])
    return True


@login_required
def solicitud_baja_resolver(request, solicitud_id):
    perfil = _perfil(request)
    if perfil is None or perfil.rol not in ROLES_RESUELVEN_BAJAS:
        return redirect("inicio")
    if request.method != "POST":
        return redirect("solicitudes_baja_lista")

    accion = request.POST.get("accion", "")
    comentario = request.POST.get("comentario", "").strip()[:1000]

    with transaction.atomic():
        solicitud = get_object_or_404(
            _solicitudes_visibles(perfil).select_for_update(), pk=solicitud_id,
        )
        if solicitud.estado != SolicitudBajaArrendatario.Estado.PENDIENTE:
            messages.error(request, "Esta solicitud ya fue resuelta.")
            return redirect("solicitudes_baja_lista")

        unidad = solicitud.unidad
        arrendatario = solicitud.arrendatario
        etiqueta_unidad = f"unidad {unidad.numero_unidad} ({unidad.edificio.nombre})"

        if accion == "aprobar":
            if unidad.arrendatario_id == arrendatario.pk:
                unidad.arrendatario = None
                unidad.cantidad_residentes = None  # el dueño debe informar quiénes viven ahora
                unidad.save(update_fields=["arrendatario", "cantidad_residentes"])
            cuenta_desactivada = _desactivar_si_no_tiene_otras_unidades(arrendatario)

            solicitud.estado = SolicitudBajaArrendatario.Estado.APROBADA
            mensaje_dueno = (
                f"El administrador aprobó la baja de {arrendatario} en la {etiqueta_unidad}. "
                "Recuerda actualizar cuántas personas viven ahora en la unidad."
            )
            if cuenta_desactivada:
                messages.success(request, f"Baja aprobada. {arrendatario} quedó desvinculado y su cuenta fue desactivada.")
            else:
                messages.success(
                    request,
                    f"Baja aprobada. {arrendatario} quedó desvinculado de esta unidad; "
                    "su cuenta sigue activa porque está ligado a otra unidad.",
                )
                notificar(
                    [arrendatario],
                    f"Se aprobó tu baja como arrendatario de la {etiqueta_unidad}.",
                    remitente=perfil,
                )

        elif accion == "rechazar":
            solicitud.estado = SolicitudBajaArrendatario.Estado.RECHAZADA
            mensaje_dueno = f"El administrador rechazó la solicitud de baja de {arrendatario} en la {etiqueta_unidad}."
            if comentario:
                mensaje_dueno += f" Comentario: {comentario}"
            notificar(
                [arrendatario],
                f"El administrador rechazó la solicitud de baja de tu arriendo en la {etiqueta_unidad}. Sigues registrado como arrendatario.",
                remitente=perfil,
            )
            messages.success(request, "Solicitud rechazada. Avisamos al dueño y al arrendatario.")

        else:
            messages.error(request, "Acción no válida.")
            return redirect("solicitudes_baja_lista")

        solicitud.resuelto_por = perfil
        solicitud.resuelto_en = timezone.now()
        solicitud.comentario_admin = comentario
        solicitud.save(update_fields=["estado", "resuelto_por", "resuelto_en", "comentario_admin"])
        notificar([solicitud.solicitante], mensaje_dueno, remitente=perfil, url=reverse("mi_unidad"))

    return redirect("solicitudes_baja_lista")

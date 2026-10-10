import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Unidad, Usuario
from .forms import AceptarInvitacionForm, InvitarForm
from .models import Invitacion, VIGENCIA
from .permisos import PUEDE_INVITAR

logger = logging.getLogger("invitaciones")


def _enviar(request, inv, token):
    link = request.build_absolute_uri(reverse("invitaciones:aceptar", args=[token]))
    unidad = ""
    if inv.unidad_id:
        unidad = f"Unidad asignada: {inv.unidad} ({inv.get_relacion_display().lower()}).\n\n"
    send_mail(
        subject="Te invitaron a CondoGestión",
        message=(
            f"Hola,\n\n"
            f"{inv.invitado_por} te invitó a CondoGestión como {inv.get_rol_display()} "
            f"del condominio {inv.condominio}.\n\n"
            f"{unidad}"
            f"Crea tu cuenta aquí (el link sirve una sola vez y vence en {VIGENCIA.days} días):\n{link}\n\n"
            f"Si no esperabas este correo, puedes ignorarlo."
        ),
        from_email=None,  # usa DEFAULT_FROM_EMAIL
        recipient_list=[inv.email],
    )


@login_required
def invitar(request):
    perfil = getattr(request.user, "usuario", None)
    if perfil is None or not perfil.activo or perfil.rol not in PUEDE_INVITAR:
        raise PermissionDenied

    form = InvitarForm(request.POST or None, perfil=perfil)
    resultado = None

    if request.method == "POST" and form.is_valid():
        rol = form.cleaned_data["rol"]
        condominio = form.cleaned_data["condominio_final"]
        unidad = form.cleaned_data.get("unidad")
        relacion = form.cleaned_data.get("relacion") or ""
        enviados, omitidos, fallidos = [], [], []

        for email in form.cleaned_data["emails"]:
            if Usuario.objects.filter(email__iexact=email).exists():
                omitidos.append(email)  # ya tiene cuenta
                continue
            with transaction.atomic():
                # Reenviar = invalidar lo anterior, así nunca hay dos links vivos para el mismo correo
                Invitacion.vigentes().filter(email=email).update(revocada=True)
                inv, token = Invitacion.crear(
                    email=email, rol=rol, condominio=condominio, invitado_por=perfil,
                    unidad=unidad, relacion=relacion,
                )
                try:
                    _enviar(request, inv, token)
                    enviados.append(email)
                except Exception:
                    transaction.set_rollback(True)  # no dejar invitaciones que nadie recibió
                    fallidos.append(email)

        resultado = {"enviados": enviados, "omitidos": omitidos, "fallidos": fallidos}
        form = InvitarForm(perfil=perfil)

    return render(request, "invitaciones/invitar.html", {"form": form, "resultado": resultado})


def _crear_cuenta(form, inv):
    """Devuelve "ok", "invalida" (link ya no sirve) o "conflicto" (error agregado al formulario)."""
    User = get_user_model()
    with transaction.atomic():
        # Re-lee con bloqueo para que dos envíos simultáneos no creen dos cuentas
        inv = Invitacion.vigentes().select_for_update().filter(pk=inv.pk).first()
        if inv is None:
            return "invalida"

        unidad = None
        if inv.unidad_id:
            # La unidad se bloquea para que dos invitados no tomen el mismo cupo a la vez
            unidad = Unidad.objects.select_for_update().get(pk=inv.unidad_id)
            if getattr(unidad, f"{inv.relacion}_id"):
                form.add_error(
                    None,
                    f"Esa unidad ya tiene un {inv.get_relacion_display().lower()} asignado. "
                    "Avisa a quien te invitó.",
                )
                return "conflicto"

        try:
            with transaction.atomic():  # savepoint: un choque de username no rompe la transacción externa
                user = User.objects.create_user(
                    username=form.cleaned_data["username"],
                    email=inv.email,
                    password=form.cleaned_data["password1"],
                )
        except IntegrityError:
            form.add_error("username", "Ese nombre de usuario ya está en uso.")
            return "conflicto"

        perfil = form.save(commit=False)
        perfil.user = user
        perfil.email = inv.email
        perfil.rol = inv.rol
        perfil.condominio = inv.condominio
        perfil.save()

        if unidad:
            setattr(unidad, inv.relacion, perfil)  # unidad.dueno o unidad.arrendatario
            unidad.cantidad_residentes = form.cleaned_data["cantidad_residentes"]
            unidad.save(update_fields=[inv.relacion, "cantidad_residentes"])

        inv.usada = timezone.now()
        inv.save(update_fields=["usada"])
    return "ok"


def aceptar(request, token):
    inv = Invitacion.buscar(token)
    # Mismo mensaje para inexistente, vencido, usado o revocado: no se filtra información
    if inv is None or Usuario.objects.filter(email__iexact=inv.email).exists():
        resp = render(request, "invitaciones/aceptar.html", {"invalida": True}, status=404)
    else:
        form = AceptarInvitacionForm(request.POST or None, invitacion=inv)
        resp = None
        if request.method == "POST" and form.is_valid():
            resultado = _crear_cuenta(form, inv)
            if resultado == "ok":
                messages.success(request, "Tu cuenta está lista. Ya puedes iniciar sesión.")
                return redirect("login")
            if resultado == "invalida":
                resp = render(request, "invitaciones/aceptar.html", {"invalida": True}, status=404)
        if resp is None:
            if request.method == "POST":
                logger.warning("Invitación rechazada para %s: %s", inv.email, form.errors.as_json())
            resp = render(request, "invitaciones/aceptar.html", {"form": form, "inv": inv})
    # El token va en la URL: que no viaje a otros sitios. OJO: "no-referrer" haría que el navegador
    # mande "Origin: null" en el POST y Django lo rechazaría (CSRF). "same-origin" evita ambos problemas.
    resp["Referrer-Policy"] = "same-origin"
    resp["Cache-Control"] = "no-store"
    return resp
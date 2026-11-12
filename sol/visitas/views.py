from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Unidad
from core.paginacion import paginar
from .models import Visita


ROLES_VISITAS = ("Super Administrador", "Administrador", "Gestor", "Residente")


def perfil_actual(request):
    return request.user.usuario


def es_super(usuario):
    return usuario.rol == "Super Administrador"


def puede_gestionar(usuario):
    return usuario.rol in ("Super Administrador", "Administrador", "Gestor")


def unidades_visibles(usuario):
    unidades = Unidad.objects.select_related("edificio", "edificio__condominio")
    if es_super(usuario):
        return unidades.order_by("edificio__condominio__nombre", "edificio__nombre", "numero_unidad")
    if usuario.rol == "Residente":
        return unidades.filter(Q(dueno=usuario) | Q(arrendatario=usuario)).distinct().order_by(
            "edificio__nombre", "numero_unidad"
        )
    condominios = usuario.condominios_asignados()
    if not condominios.exists():
        return unidades.none()
    return unidades.filter(edificio__condominio__in=condominios).order_by(
        "edificio__nombre", "numero_unidad"
    )


def redirigir_lista(fecha=None):
    url = reverse("visitas:lista")
    return redirect(f"{url}?fecha={fecha.isoformat()}" if fecha else url)


@login_required
def lista(request):
    usuario = perfil_actual(request)
    if usuario.rol not in ROLES_VISITAS:
        return redirect("inicio")
    if not es_super(usuario) and not usuario.condominios_asignados().exists():
        messages.error(request, "Tu usuario todavía no tiene un condominio asignado.")
        return redirect("inicio")

    hoy = timezone.localdate()
    unidades = unidades_visibles(usuario)
    condominios_filtro = usuario.condominios_asignados().filter(activo=True).order_by("nombre") if puede_gestionar(usuario) else None
    visitas = Visita.objects.select_related(
        "condominio", "edificio", "unidad", "unidad__edificio", "registrado_por"
    )
    if usuario.rol == "Residente":
        visitas = visitas.filter(unidad__in=unidades)
    elif not es_super(usuario):
        visitas = visitas.filter(condominio__in=usuario.condominios_asignados())

    condominio_filtro = request.GET.get("condominio", "").strip() if puede_gestionar(usuario) else ""
    if condominio_filtro.isdigit() and condominios_filtro.filter(pk=condominio_filtro).exists():
        visitas = visitas.filter(condominio_id=condominio_filtro)
        unidades = unidades.filter(edificio__condominio_id=condominio_filtro)
    else:
        condominio_filtro = ""

    # Registrar este cambio automático en el historial administrativo.
    visitas_vencidas = visitas.filter(fecha_visita__lt=hoy, estado=Visita.Estado.PENDIENTE)
    for visita_vencida in visitas_vencidas:
        visita_vencida.estado = Visita.Estado.SIN_REGISTRO
        visita_vencida.save(update_fields=["estado"])

    ver_todas = request.GET.get("todas") == "1"
    fecha_filtro = request.GET.get("fecha", "")
    if not ver_todas and not fecha_filtro and puede_gestionar(usuario):
        fecha_filtro = hoy.isoformat()
    if fecha_filtro:
        try:
            visitas = visitas.filter(fecha_visita=date.fromisoformat(fecha_filtro))
        except ValueError:
            fecha_filtro = ""

    unidad_filtro = request.GET.get("unidad", "").strip()
    if unidad_filtro:
        try:
            unidad_id = int(unidad_filtro)
        except ValueError:
            unidad_filtro = ""
        else:
            if unidades.filter(pk=unidad_id).exists():
                visitas = visitas.filter(unidad_id=unidad_id)
            else:
                unidad_filtro = ""

    nombre_filtro = request.GET.get("nombre", "").strip()
    if nombre_filtro:
        visitas = visitas.filter(nombre__icontains=nombre_filtro)
    patente_filtro = request.GET.get("patente", "").strip()
    if patente_filtro:
        visitas = visitas.filter(patente_vehiculo__icontains=patente_filtro)
    estado_filtro = request.GET.get("estado", "").strip()
    estados_validos = [value for value, _label in Visita.Estado.choices]
    if estado_filtro in estados_validos:
        visitas = visitas.filter(estado=estado_filtro)
    else:
        estado_filtro = ""

    orden_visitas = ("fecha_visita", "tiempo_llegada", "nombre", "pk") if fecha_filtro else ("-fecha_visita", "-tiempo_llegada", "-pk")
    paginacion = paginar(request, visitas.order_by(*orden_visitas))
    return render(request, "visitas/lista.html", {
        "usuario_actual": usuario,
        "visitas": paginacion["page_obj"],
        "unidades": unidades,
        "fecha_filtro": fecha_filtro,
        "unidad_filtro": unidad_filtro,
        "nombre_filtro": nombre_filtro,
        "patente_filtro": patente_filtro,
        "estado_filtro": estado_filtro,
        "estados": Visita.Estado.choices,
        "hoy": hoy,
        "es_fecha_hoy": fecha_filtro == hoy.isoformat(),
        "ver_todas": ver_todas,
        "puede_gestionar": puede_gestionar(usuario),
        "condominios_filtro": condominios_filtro,
        "condominio_filtro": condominio_filtro,
        **paginacion,
    })


@login_required
def crear(request):
    usuario = perfil_actual(request)
    if usuario.rol not in ROLES_VISITAS:
        return redirect("inicio")
    if not es_super(usuario) and not usuario.condominios_asignados().exists():
        messages.error(request, "Tu usuario todavía no tiene un condominio asignado.")
        return redirect("inicio")

    unidades = unidades_visibles(usuario).filter(activo=True, edificio__activo=True)
    errores = []
    if request.method == "POST":
        unidad_id = request.POST.get("unidad", "").strip()
        nombre = request.POST.get("nombre", "").strip()
        fecha_texto = request.POST.get("fecha_visita", "").strip()
        motivo = request.POST.get("motivo", "").strip()
        patente = request.POST.get("patente_vehiculo", "").strip().upper()
        try:
            unidad = unidades.filter(pk=int(unidad_id)).first() if unidad_id else None
        except (TypeError, ValueError):
            unidad = None

        if not unidad:
            errores.append("Selecciona una unidad a la que se dirige la visita.")
        if not nombre:
            errores.append("Ingresa el nombre de la visita.")
        if len(patente) > 10:
            errores.append("La patente no puede superar 10 caracteres.")
        try:
            fecha_visita = date.fromisoformat(fecha_texto)
            if fecha_visita < timezone.localdate():
                errores.append("La fecha de visita no puede estar en el pasado.")
        except ValueError:
            fecha_visita = None
            errores.append("Indica una fecha de visita válida.")

        if not errores:
            Visita.objects.create(
                condominio=unidad.edificio.condominio,
                edificio=unidad.edificio,
                unidad=unidad,
                registrado_por=usuario,
                nombre=nombre,
                fecha_visita=fecha_visita,
                motivo=motivo,
                patente_vehiculo=patente,
            )
            messages.success(request, "La visita quedó registrada para la fecha indicada.")
            return redirigir_lista(fecha_visita)

    return render(request, "visitas/crear.html", {
        "usuario_actual": usuario,
        "unidades": unidades,
        "errores": errores,
        "datos": request.POST if request.method == "POST" else {},
        "hoy": timezone.localdate().isoformat(),
        "es_residente": usuario.rol == "Residente",
    })


@login_required
def marcar_presente(request, visita_id):
    if request.method != "POST":
        return redirect("visitas:lista")
    usuario = perfil_actual(request)
    if not puede_gestionar(usuario):
        messages.error(request, "Solo el personal de acceso puede marcar llegadas.")
        return redirect("visitas:lista")

    visita = get_object_or_404(Visita.objects.select_related("condominio"), id=visita_id)
    if not usuario.puede_acceder_condominio(visita.condominio_id):
        messages.error(request, "Esta visita no pertenece a tu condominio.")
        return redirect("visitas:lista")

    hoy = timezone.localdate()
    if visita.estado == Visita.Estado.PRESENTE:
        visita.estado = Visita.Estado.FINALIZADA
        visita.tiempo_salida = timezone.now()
        visita.save(update_fields=["estado", "tiempo_salida"])
        messages.success(request, "Salida registrada correctamente.")
    elif visita.fecha_visita < hoy and visita.estado == Visita.Estado.PENDIENTE:
        visita.estado = Visita.Estado.SIN_REGISTRO
        visita.save(update_fields=["estado"])
        messages.info(request, "La fecha programada ya pasó; la visita quedó como Sin registro.")
    elif visita.fecha_visita != hoy:
        messages.error(request, "Solo puedes marcar como presente una visita programada para hoy.")
    elif visita.estado != Visita.Estado.PENDIENTE:
        messages.info(request, "Esta visita ya tiene un estado registrado.")
    else:
        visita.estado = Visita.Estado.PRESENTE
        visita.tiempo_llegada = timezone.now()
        visita.save(update_fields=["estado", "tiempo_llegada"])
        llegada_local = timezone.localtime(visita.tiempo_llegada)
        messages.success(request, f"Llegada registrada a las {llegada_local:%H:%M}.")

    return redirigir_lista(visita.fecha_visita)


@login_required
def editar(request, visita_id):
    usuario = perfil_actual(request)
    visita = get_object_or_404(Visita.objects.select_related("unidad__edificio__condominio", "registrado_por"), pk=visita_id)
    unidades = unidades_visibles(usuario).filter(activo=True, edificio__activo=True)
    es_personal = puede_gestionar(usuario) and usuario.puede_acceder_condominio(visita.condominio_id)
    es_autor = visita.registrado_por_id == usuario.id
    if not (es_personal or (usuario.rol == "Residente" and es_autor)):
        messages.error(request, "No tienes permiso para editar esta visita.")
        return redirigir_lista()
    if visita.estado != Visita.Estado.PENDIENTE or visita.fecha_visita < timezone.localdate():
        messages.error(request, "Solo puedes editar una visita futura que aún no haya llegado.")
        return redirigir_lista(visita.fecha_visita)

    datos = {
        "unidad": str(visita.unidad_id),
        "nombre": visita.nombre,
        "fecha_visita": visita.fecha_visita.isoformat(),
        "motivo": visita.motivo,
        "patente_vehiculo": visita.patente_vehiculo,
    }
    errores = []
    if request.method == "POST":
        unidad_id = request.POST.get("unidad", "").strip()
        unidad = unidades.filter(pk=unidad_id).first()
        nombre = request.POST.get("nombre", "").strip()
        fecha_texto = request.POST.get("fecha_visita", "").strip()
        motivo = request.POST.get("motivo", "").strip()
        patente = request.POST.get("patente_vehiculo", "").strip().upper()
        datos = request.POST
        if not unidad:
            errores.append("Selecciona una unidad activa y autorizada.")
        if not nombre:
            errores.append("Ingresa el nombre de la visita.")
        if len(patente) > 10:
            errores.append("La patente no puede superar 10 caracteres.")
        try:
            fecha_visita = date.fromisoformat(fecha_texto)
            if fecha_visita < timezone.localdate():
                errores.append("La fecha de visita no puede estar en el pasado.")
        except ValueError:
            fecha_visita = None
            errores.append("Indica una fecha de visita válida.")
        if not errores:
            visita.unidad = unidad
            visita.edificio = unidad.edificio
            visita.condominio = unidad.edificio.condominio
            visita.nombre = nombre
            visita.fecha_visita = fecha_visita
            visita.motivo = motivo
            visita.patente_vehiculo = patente
            visita.save()
            messages.success(request, "Se actualizaron los datos de la visita.")
            return redirigir_lista(fecha_visita)
    return render(request, "visitas/crear.html", {
        "usuario_actual": usuario,
        "unidades": unidades,
        "errores": errores,
        "datos": datos,
        "hoy": timezone.localdate().isoformat(),
        "es_residente": usuario.rol == "Residente",
        "edicion": True,
        "visita": visita,
    })


@login_required
def cancelar(request, visita_id):
    if request.method != "POST":
        return redirect("visitas:lista")
    usuario = perfil_actual(request)
    visita = get_object_or_404(Visita.objects.select_related("condominio", "registrado_por"), pk=visita_id)
    es_personal = puede_gestionar(usuario) and usuario.puede_acceder_condominio(visita.condominio_id)
    es_autor = visita.registrado_por_id == usuario.id
    if not (es_personal or (usuario.rol == "Residente" and es_autor)):
        messages.error(request, "No tienes permiso para cancelar esta visita.")
    elif visita.estado != Visita.Estado.PENDIENTE or visita.fecha_visita < timezone.localdate():
        messages.error(request, "Solo puedes cancelar una visita pendiente que todavía no haya ocurrido.")
    else:
        visita.estado = Visita.Estado.CANCELADA
        visita.save(update_fields=["estado"])
        messages.success(request, "La visita quedó cancelada.")
    return redirigir_lista(visita.fecha_visita)

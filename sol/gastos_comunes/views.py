from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from core.paginacion import paginar
from django.db import OperationalError, transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseServerError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.models import Condominio, Unidad, Usuario
from .forms import ItemGastoForm, PagoForm, PeriodoForm
from .models import ItemGasto, LecturaMedidor, Liquidacion, MercadoPagoConexion, MercadoPagoIntentoOAuth, PagoOnline, PagoRegistrado, Periodo
from .services import calcular_periodo, publicar_periodo, resumen_mora, saldos_acumulados
from .excel import crear_plantilla, leer_tabla
from .mercadopago import MercadoPagoError, crear_checkout, crear_intento_oauth, completar_oauth, es_modo_prueba, localizar_pago_webhook, secreto_webhook_actual, sincronizar_pago, verificar_firma_webhook

ROLES_GESTION = {Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR}


def perfil(request):
    return request.user.usuario


def condominios_permitidos(usuario):
    return usuario.condominios_asignados().filter(activo=True)


def error_formulario(exc):
    return "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


@login_required
def lista(request):
    usuario = perfil(request)
    if usuario.rol in ROLES_GESTION:
        periodos = Periodo.objects.filter(condominio__in=condominios_permitidos(usuario)).select_related("condominio").order_by("-anio", "-mes", "condominio__nombre", "-pk")
        paginacion = paginar(request, periodos)
        return render(request, "gastos_comunes/lista.html", {"usuario": usuario, "periodos": paginacion["page_obj"], "es_gestion": True, "puede_configurar_mp": usuario.rol in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER), **paginacion})
    unidades = Unidad.objects.filter(Q(dueno=usuario) | Q(arrendatario=usuario)).distinct()
    liquidaciones_qs = Liquidacion.objects.filter(unidad__in=unidades, periodo__estado=Periodo.Estado.PUBLICADO).select_related("periodo", "unidad", "unidad__edificio", "periodo__condominio").prefetch_related("pagos").order_by("-periodo__anio", "-periodo__mes", "-pk")
    paginacion = paginar(request, liquidaciones_qs)
    liquidaciones = paginacion["page_obj"]
    condominios_mora = {liq.periodo.condominio_id: liq.periodo.condominio for liq in liquidaciones}
    moras_por_condominio = {pk: resumen_mora(condominio) for pk, condominio in condominios_mora.items()}
    for liquidacion in liquidaciones.object_list:
        liquidacion.saldo_actual = saldos_acumulados(liquidacion.periodo.condominio, liquidacion.periodo).get(liquidacion.unidad_id, Decimal("0"))
        mora = moras_por_condominio.get(liquidacion.periodo.condominio_id, {}).get(liquidacion.unidad_id, {})
        liquidacion.en_mora = liquidacion.saldo_actual > 0 and (
            mora.get("principal_vencido", Decimal("0")) > 0
            or mora.get("interes_devengado", Decimal("0")) > 0
        )
    return render(request, "gastos_comunes/lista.html", {"usuario": usuario, "liquidaciones": liquidaciones, "es_gestion": False, **paginacion})


@login_required
def periodo_nuevo(request):
    usuario = perfil(request)
    if usuario.rol not in ROLES_GESTION:
        messages.error(request, "No tienes permiso para crear períodos.")
        return redirect("gastos_comunes:lista")
    form = PeriodoForm(request.POST or None, condominios=condominios_permitidos(usuario))
    if request.method == "POST" and form.is_valid():
        periodo = form.save(commit=False)
        periodo.creado_por = usuario
        try:
            periodo.save()
        except Exception as exc:
            form.add_error(None, "Ya existe un período para ese condominio y mes." if "UNIQUE" in str(exc).upper() else str(exc))
        else:
            messages.success(request, "Período creado. Agrega los gastos y lecturas para continuar.")
            return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
    return render(request, "gastos_comunes/periodo_form.html", {"form": form})


@login_required
def periodo_eliminar(request, periodo_id):
    usuario = perfil(request)
    if request.method != "POST" or usuario.rol not in ROLES_GESTION:
        return redirect("gastos_comunes:lista")
    periodo = get_object_or_404(Periodo.objects.select_related("condominio"), pk=periodo_id)
    if not usuario.puede_acceder_condominio(periodo.condominio_id):
        messages.error(request, "No tienes acceso a este condominio.")
        return redirect("gastos_comunes:lista")
    if periodo.estado != Periodo.Estado.BORRADOR:
        messages.error(request, "Solo se pueden eliminar períodos que siguen en borrador.")
        return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
    liquidaciones = periodo.liquidaciones.all()
    if liquidaciones.filter(pagos__isnull=False).exists() or liquidaciones.filter(pagos_online__isnull=False).exists():
        messages.error(request, "El borrador tiene movimientos asociados y no se puede eliminar.")
        return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
    periodo.delete()
    messages.success(request, "Se eliminó el período que estaba en borrador.")
    return redirect("gastos_comunes:lista")


@login_required
def detalle(request, periodo_id):
    usuario = perfil(request)
    periodo = get_object_or_404(Periodo.objects.select_related("condominio"), pk=periodo_id)
    if not usuario.puede_acceder_condominio(periodo.condominio_id):
        messages.error(request, "No tienes acceso a este condominio.")
        return redirect("gastos_comunes:lista")
    es_gestion = usuario.rol in ROLES_GESTION
    if not es_gestion:
        unidad_ids = Unidad.objects.filter(dueno=usuario).values_list("id", flat=True)
        unidad_ids = list(unidad_ids) + list(Unidad.objects.filter(arrendatario=usuario).values_list("id", flat=True))
        liquidaciones = Liquidacion.objects.filter(periodo=periodo, unidad_id__in=unidad_ids, periodo__estado=Periodo.Estado.PUBLICADO).prefetch_related("detalles", "pagos")
        liquidaciones = list(liquidaciones)
        saldos = saldos_acumulados(periodo.condominio, periodo)
        moras = resumen_mora(periodo.condominio)
        for liquidacion in liquidaciones:
            liquidacion.saldo_actual = saldos.get(liquidacion.unidad_id, Decimal("0"))
            mora = moras.get(liquidacion.unidad_id, {})
            liquidacion.en_mora = liquidacion.saldo_actual > 0 and (
                mora.get("principal_vencido", Decimal("0")) > 0
                or mora.get("interes_devengado", Decimal("0")) > 0
            )
        ultimo_periodo = Periodo.objects.filter(
            condominio=periodo.condominio, estado=Periodo.Estado.PUBLICADO
        ).order_by("-anio", "-mes").first()
        liquidacion_ids = [liquidacion.id for liquidacion in liquidaciones]
        pagos_online = PagoOnline.objects.filter(
            liquidacion_id__in=liquidacion_ids, usuario=usuario
        ).order_by("-creado")[:10]
        cuenta_conectada = MercadoPagoConexion.objects.filter(condominio=periodo.condominio).exists()
        return render(request, "gastos_comunes/residente_detalle.html", {
            "periodo": periodo,
            "liquidaciones": liquidaciones,
            "es_ultimo_periodo": periodo == ultimo_periodo,
            "puede_pagar_en_linea": periodo == ultimo_periodo and cuenta_conectada,
            "pagos_online_configurados": cuenta_conectada,
            "pagos_online": pagos_online,
        })

    item_form = ItemGastoForm(periodo=periodo)
    pago_form = PagoForm()
    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "agregar_gasto" and periodo.estado == Periodo.Estado.BORRADOR:
            item_form = ItemGastoForm(request.POST, request.FILES, periodo=periodo)
            if item_form.is_valid():
                item = item_form.save(commit=False)
                item.periodo = periodo
                try:
                    item.full_clean(); item.save()
                    messages.success(request, "Gasto agregado al borrador.")
                    return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
                except ValidationError as exc:
                    item_form.add_error(None, error_formulario(exc))
        elif accion == "calcular" and periodo.estado == Periodo.Estado.BORRADOR:
            try:
                cantidad = calcular_periodo(periodo)
                messages.success(request, f"Cálculo actualizado para {cantidad} unidades. Revisa el detalle antes de publicar.")
                return redirect(f"{reverse('gastos_comunes:detalle', kwargs={'periodo_id': periodo.pk})}?calculado=1#vista-previa")
            except ValidationError as exc:
                messages.error(request, error_formulario(exc))
        elif accion == "publicar" and periodo.estado == Periodo.Estado.BORRADOR:
            try:
                publicar_periodo(periodo)
                messages.success(request, "Liquidaciones publicadas para los residentes.")
                return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
            except ValidationError as exc:
                messages.error(request, error_formulario(exc))
        elif accion == "pago" and periodo.estado == Periodo.Estado.PUBLICADO:
            liquidacion = get_object_or_404(Liquidacion, pk=request.POST.get("liquidacion_id"), periodo=periodo)
            pago_form = PagoForm(request.POST)
            if pago_form.is_valid():
                pago = pago_form.save(commit=False)
                pago.liquidacion = liquidacion
                pago.registrado_por = usuario
                ultimo_periodo = Periodo.objects.filter(condominio=periodo.condominio, estado=Periodo.Estado.PUBLICADO).order_by("-anio", "-mes").first()
                saldo = saldos_acumulados(periodo.condominio, ultimo_periodo).get(liquidacion.unidad_id, Decimal("0"))
                if ultimo_periodo != periodo:
                    pago_form.add_error(None, "Registra los pagos desde el período publicado más reciente para mantener correcto el saldo acumulado.")
                elif pago.monto > saldo:
                    pago_form.add_error("monto", "El pago supera el saldo pendiente de esta unidad.")
                else:
                    pago.save()
                    messages.success(request, "Pago registrado.")
                    return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
        elif accion == "coeficientes" and periodo.estado == Periodo.Estado.BORRADOR:
            try:
                importar_alicuotas(request, periodo)
                messages.success(request, "Alícuotas importadas. Verifica que sumen 100% antes de calcular.")
                return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
            except (ValueError, ValidationError, KeyError, InvalidOperation, UnicodeDecodeError) as exc:
                messages.error(request, f"No se importó el archivo: {error_formulario(exc)}")
        elif accion == "alicuotas_iguales" and periodo.estado == Periodo.Estado.BORRADOR:
            unidades_activas = list(Unidad.objects.filter(
                edificio__condominio=periodo.condominio,
                edificio__activo=True,
                activo=True,
            ).order_by("pk"))
            if not unidades_activas:
                messages.error(request, "El condominio no tiene unidades activas para asignar alícuotas.")
            else:
                precision = Decimal("0.000001")
                valor_base = (Decimal("100") / len(unidades_activas)).quantize(
                    precision, rounding=ROUND_HALF_UP
                )
                diferencia = int(((Decimal("100") - valor_base * len(unidades_activas)) / precision).to_integral_value())
                ajuste = precision if diferencia > 0 else -precision
                for indice, unidad in enumerate(unidades_activas):
                    unidad.alicuota = valor_base + (ajuste if indice < abs(diferencia) else Decimal("0"))
                Unidad.objects.bulk_update(unidades_activas, ["alicuota"])
                messages.success(
                    request,
                    f"Se asignaron alícuotas iguales a {len(unidades_activas)} unidades activas. "
                    "La suma queda en 100%; puedes cambiar los valores según el reglamento.",
                )
                return redirect(f"{reverse('gastos_comunes:detalle', kwargs={'periodo_id': periodo.pk})}?alicuotas_actualizadas=1#alicuotas-unidades")
        elif accion == "lecturas" and periodo.estado == Periodo.Estado.BORRADOR:
            try:
                cantidad = importar_lecturas(request, periodo)
                messages.success(request, f"Se importaron {cantidad} lecturas.")
                return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
            except (ValueError, ValidationError, KeyError, InvalidOperation, UnicodeDecodeError) as exc:
                messages.error(request, f"No se importaron las lecturas: {error_formulario(exc)}")

    liquidaciones = list(periodo.liquidaciones.select_related("unidad", "unidad__edificio").prefetch_related("pagos", "detalles").order_by("unidad__edificio__numero", "unidad__numero_unidad"))
    saldos = saldos_acumulados(periodo.condominio, periodo) if periodo.estado == Periodo.Estado.PUBLICADO else {}
    moras = resumen_mora(periodo.condominio) if periodo.estado == Periodo.Estado.PUBLICADO else {}
    grupos_por_edificio = {}
    for liq in liquidaciones:
        liq.pagado_calculado = sum((p.monto for p in liq.pagos.all()), Decimal("0"))
        liq.saldo_actual = saldos.get(liq.unidad_id, Decimal("0"))
        mora = moras.get(liq.unidad_id, {})
        liq.en_mora = liq.saldo_actual > 0 and (
            mora.get("principal_vencido", Decimal("0")) > 0
            or mora.get("interes_devengado", Decimal("0")) > 0
        )
        grupo = grupos_por_edificio.setdefault(liq.unidad.edificio_id, {
            "id": liq.unidad.edificio_id, "nombre": liq.unidad.edificio.nombre,
            "unidades": [], "total": Decimal("0"),
        })
        grupo["unidades"].append(liq)
        grupo["total"] += liq.total
    grupos_liquidacion = list(grupos_por_edificio.values())
    gastos = list(periodo.gastos.select_related("edificio", "unidad"))
    lecturas = list(periodo.lecturas.select_related("unidad", "unidad__edificio").order_by("unidad__edificio__numero", "unidad__numero_unidad", "tipo"))
    unidades_activas = list(Unidad.objects.filter(
        edificio__condominio=periodo.condominio,
        edificio__activo=True,
        activo=True,
    ).only("alicuota"))
    alicuotas_configuradas = sum(1 for unidad in unidades_activas if unidad.alicuota is not None)
    suma_alicuotas = sum((unidad.alicuota or Decimal("0") for unidad in unidades_activas), Decimal("0"))
    ultimo_periodo = Periodo.objects.filter(condominio=periodo.condominio, estado=Periodo.Estado.PUBLICADO).order_by("-anio", "-mes").first()
    return render(request, "gastos_comunes/periodo_detalle.html", {
        "periodo": periodo, "gastos": gastos,
        "lecturas": lecturas, "liquidaciones": liquidaciones,
        "grupos_liquidacion": grupos_liquidacion,
        "total_liquidaciones": sum((liq.total for liq in liquidaciones), Decimal("0")),
        "item_form": item_form, "pago_form": pago_form,
        "es_gestion": True, "unidades_sin_alicuota": Unidad.objects.filter(edificio__condominio=periodo.condominio, edificio__activo=True, activo=True, alicuota__isnull=True).count(),
        "unidades_activas_count": len(unidades_activas),
        "alicuotas_configuradas": alicuotas_configuradas,
        "suma_alicuotas": suma_alicuotas,
        "today": timezone.localdate(),
        "puede_registrar_pagos": ultimo_periodo == periodo,
    })


@login_required
@require_POST
def iniciar_pago_online(request, liquidacion_id):
    usuario = perfil(request)
    if usuario.rol != Usuario.Rol.RESIDENTE:
        messages.error(request, "Los pagos en línea de prueba están disponibles para residentes.")
        return redirect("gastos_comunes:lista")
    liquidacion = get_object_or_404(
        Liquidacion.objects.select_related("periodo__condominio", "unidad__edificio"),
        pk=liquidacion_id,
        periodo__estado=Periodo.Estado.PUBLICADO,
    )
    if liquidacion.unidad.dueno_id != usuario.pk and liquidacion.unidad.arrendatario_id != usuario.pk:
        return HttpResponseForbidden("No tienes permiso para pagar esta unidad.")
    if not MercadoPagoConexion.objects.filter(condominio=liquidacion.periodo.condominio).exists():
        messages.error(request, "El condominio todavía no tiene una cuenta vendedora de Mercado Pago conectada.")
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)

    ultimo_periodo = Periodo.objects.filter(
        condominio=liquidacion.periodo.condominio, estado=Periodo.Estado.PUBLICADO
    ).order_by("-anio", "-mes").first()
    if ultimo_periodo != liquidacion.periodo:
        messages.error(request, "Inicia el pago desde el período publicado más reciente.")
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)

    saldo = saldos_acumulados(liquidacion.periodo.condominio, ultimo_periodo).get(
        liquidacion.unidad_id, Decimal("0")
    )
    monto = saldo.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if monto <= 0:
        messages.info(request, "Esta unidad no tiene saldo pendiente.")
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)

    intento_abierto = PagoOnline.objects.filter(
        liquidacion=liquidacion,
        usuario=usuario,
        estado__in=[PagoOnline.Estado.CREADO, PagoOnline.Estado.PENDIENTE],
    ).order_by("-creado").first()
    if intento_abierto:
        if intento_abierto.monto != monto:
            messages.error(request, "Ya hay un pago en línea pendiente para esta unidad. Revisa su estado antes de iniciar otro.")
            return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
        # Reutilizar sólo una orden en curso. Las preferencias antiguas con
        # error en el checkout deben generar un intento nuevo.
        if intento_abierto.url_pago and intento_abierto.orden_proveedor:
            return redirect(intento_abierto.url_pago)
        if not intento_abierto.preferencia_proveedor:
            pago = intento_abierto
        else:
            pago = PagoOnline.objects.create(liquidacion=liquidacion, usuario=usuario, monto=monto)
    else:
        pago = PagoOnline.objects.create(liquidacion=liquidacion, usuario=usuario, monto=monto)

    sitio_url = getattr(settings, "SITIO_URL", "")
    webhook_url = getattr(settings, "MERCADOPAGO_WEBHOOK_URL", "")
    try:
        webhook_secret = secreto_webhook_actual()
    except MercadoPagoError as exc:
        messages.error(request, str(exc))
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
    if (not sitio_url.startswith("https://") or not webhook_url.startswith("https://")
            or not webhook_secret):
        pago.estado = PagoOnline.Estado.ERROR
        pago.detalle_estado = "https_urls_not_configured"
        pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        messages.error(request, "El pago en línea requiere SITIO_URL, MERCADOPAGO_WEBHOOK_URL y la clave secreta del webhook del ambiente seleccionado.")
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
    resultado_url = sitio_url + reverse(
        "gastos_comunes:pago_resultado", kwargs={"pago_id": pago.referencia_externa}
    )
    try:
        checkout_url = crear_checkout(pago, {
            "success": resultado_url,
            "failure": resultado_url,
            "pending": resultado_url,
            "notification": webhook_url,
        })
    except MercadoPagoError as exc:
        pago.estado = PagoOnline.Estado.ERROR
        pago.detalle_estado = str(exc)[:100]
        pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        messages.error(request, str(exc))
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
    return redirect(checkout_url)


@login_required
def resultado_pago_online(request, pago_id):
    usuario = perfil(request)
    pago = get_object_or_404(
        PagoOnline.objects.select_related("liquidacion__periodo", "liquidacion__unidad"),
        referencia_externa=pago_id,
    )
    if pago.usuario_id != usuario.pk:
        return HttpResponseForbidden("No tienes permiso para ver este pago.")
    payment_id = request.GET.get("payment_id") or request.GET.get("collection_id")
    order_id = request.GET.get("order_id")
    if (payment_id or order_id or pago.orden_proveedor) and pago.estado != PagoOnline.Estado.APROBADO:
        try:
            # El regreso del checkout por sí solo nunca confirma un pago.
            pago = sincronizar_pago(pago, payment_id=payment_id, order_id=order_id)
        except MercadoPagoError:
            pass
        except OperationalError:
            # Si la notificación sigue guardando el mismo pago, muestra el estado
            # que alcanzó a cargar la página y evita convertir el retorno en 500.
            try:
                pago.refresh_from_db()
            except OperationalError:
                pass
    return render(request, "gastos_comunes/pago_resultado.html", {"pago": pago})


@csrf_exempt
@require_POST
def mercadopago_webhook(request):
    try:
        secret = secreto_webhook_actual()
    except MercadoPagoError:
        return HttpResponseServerError("Ambiente de Mercado Pago no configurado.")
    if not secret:
        return HttpResponseServerError("Webhook de prueba no configurado.")
    if not verificar_firma_webhook(request, secret):
        return HttpResponseForbidden("Firma no válida.")
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest("Notificación no válida.")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("Notificación no válida.")
    resource_id = request.GET.get("data.id") or str((payload.get("data") or {}).get("id") or "")
    if not resource_id or len(resource_id) > 100:
        return HttpResponseBadRequest("Falta el identificador del recurso.")
    # Orders usan IDs alfanuméricos; los pagos del flujo legacy son numéricos.
    order_id = resource_id if not resource_id.isdigit() else ""
    payment_id = "" if order_id else resource_id
    # El webhook sólo firma el ID del recurso. Se consulta al vendedor OAuth
    # antes de asociarlo a una liquidación.
    try:
        pago = localizar_pago_webhook(payment_id=payment_id, order_id=order_id)
        if pago:
            sincronizar_pago(pago, payment_id=payment_id or None, order_id=order_id or None)
            return HttpResponse(status=200)
    except (MercadoPagoError, OperationalError):
        return HttpResponse(status=503)
    # Se solicita reintento para no perder una confirmación por una falla temporal.
    return HttpResponse(status=503)


@login_required
def mercadopago_cuentas(request):
    usuario = perfil(request)
    if usuario.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER):
        return HttpResponseForbidden("Solo un administrador autorizado puede gestionar esta cuenta.")
    condominios = condominios_permitidos(usuario).order_by("nombre")
    return render(request, "gastos_comunes/mercadopago_cuentas.html", {
        "condominios": condominios,
        "mercadopago_modo_prueba": es_modo_prueba(),
    })


@login_required
@require_POST
def mercadopago_conectar(request, condominio_id):
    usuario = perfil(request)
    if usuario.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER) or not usuario.puede_acceder_condominio(condominio_id):
        return HttpResponseForbidden("No tienes permiso para gestionar esta cuenta.")
    condominio = get_object_or_404(Condominio, pk=condominio_id, activo=True)
    try:
        _, url = crear_intento_oauth(condominio, usuario)
    except MercadoPagoError as exc:
        messages.error(request, str(exc))
        return redirect("gastos_comunes:mercadopago_cuentas")
    return redirect(url)


def mercadopago_callback(request):
    def destino_callback():
        if not request.user.is_authenticated:
            return redirect(settings.LOGIN_URL)
        usuario_actual = getattr(request.user, "usuario", None)
        if usuario_actual and usuario_actual.rol in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER):
            return redirect("gastos_comunes:mercadopago_cuentas")
        return redirect("gastos_comunes:lista")

    state = request.GET.get("state", "")
    state_hash = hashlib.sha256(state.encode()).hexdigest() if state else ""
    # OAuth can return with a different or expired browser session. The random,
    # one-use state identifies the initiating request; authorize it against the
    # original administrator stored on that request, not the current browser user.
    intento = MercadoPagoIntentoOAuth.objects.filter(
        state_hash=state_hash, consumido__isnull=True
    ).select_related("condominio", "usuario").first()
    if not intento or intento.creado < timezone.now() - timedelta(minutes=10):
        messages.error(request, "La autorización expiró o no coincide con una solicitud válida de GDC. Inicia la conexión nuevamente.")
        return destino_callback()
    iniciador = intento.usuario
    if (
        not iniciador.activo
        or iniciador.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER)
        or not iniciador.puede_acceder_condominio(intento.condominio_id)
    ):
        intento.delete()
        messages.error(request, "La cuenta GDC que inició esta autorización ya no tiene permiso para gestionar este condominio.")
        return destino_callback()
    intento.consumido = timezone.now()
    intento.save(update_fields=["consumido"])
    if request.GET.get("error"):
        intento.delete()
        messages.info(request, "No se autorizó la conexión de Mercado Pago.")
        return destino_callback()
    code = request.GET.get("code", "")
    if not code:
        intento.delete()
        messages.error(request, "Mercado Pago no devolvió el código de autorización.")
        return destino_callback()
    try:
        completar_oauth(intento, code)
        messages.success(request, f"Cuenta vendedora conectada para {intento.condominio.nombre}.")
    except MercadoPagoError as exc:
        intento.delete()
        messages.error(request, str(exc))
    return destino_callback()


@login_required
@require_POST
def mercadopago_desconectar(request, condominio_id):
    usuario = perfil(request)
    if usuario.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER) or not usuario.puede_acceder_condominio(condominio_id):
        return HttpResponseForbidden("No tienes permiso para gestionar esta cuenta.")
    MercadoPagoConexion.objects.filter(condominio_id=condominio_id).delete()
    messages.success(request, "Se desconectó la cuenta vendedora del condominio.")
    return redirect("gastos_comunes:mercadopago_cuentas")


@login_required
def descargar_plantilla(request, periodo_id, tipo):
    usuario = perfil(request)
    periodo = get_object_or_404(Periodo.objects.select_related("condominio"), pk=periodo_id)
    if usuario.rol not in ROLES_GESTION or not usuario.puede_acceder_condominio(periodo.condominio_id):
        messages.error(request, "No tienes permiso para descargar esta plantilla.")
        return redirect("gastos_comunes:lista")
    if periodo.estado != Periodo.Estado.BORRADOR:
        messages.error(request, "Las plantillas solo están disponibles mientras el período está en borrador.")
        return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
    unidades = list(Unidad.objects.filter(edificio__condominio=periodo.condominio, edificio__activo=True, activo=True).select_related("edificio").order_by("edificio__numero", "numero_unidad"))
    if tipo == "alicuotas":
        filas = [[u.edificio.nombre, u.numero_unidad, ""] for u in unidades]
        contenido = crear_plantilla(
            "Alícuotas oficiales",
            "Complete la columna amarilla con la alícuota oficial, solo con números y sin %.\nEjemplo: 0,58 significa 0,58%. La suma de todas las unidades debe ser 100%. Si son solo 2 unidades iguales, escriba 50 en cada una.",
            ["Edificio", "Unidad", "Alícuota (%)"], filas, [24, 18, 20], columnas_entrada=[3],
        )
        nombre = f"alicuotas_{slugify(periodo.condominio.nombre) or 'condominio'}_{periodo.anio}_{periodo.mes:02d}.xlsx"
    elif tipo == "lecturas":
        filas = [[u.edificio.nombre, u.numero_unidad, medidor, "", "", "", "", ""] for u in unidades for medidor in ("Agua", "Gas")]
        contenido = crear_plantilla(
            "Lecturas de medidores",
            "Complete solo los medidores que existen en la unidad. Celdas amarillas: lectura anterior, lectura actual y tarifa por unidad. El consumo y cargo estimado se calculan en Excel.",
            ["Edificio", "Unidad", "Medidor", "Lectura anterior", "Lectura actual", "Consumo (calculado)", "Tarifa ($ por unidad)", "Cargo calculado ($)"],
            filas, [22, 16, 14, 19, 19, 20, 22, 20], columnas_entrada=[4, 5, 7],
            formulas={6: 'IF(OR(D{row}="",E{row}=""),"",E{row}-D{row})', 8: 'IF(OR(F{row}="",G{row}=""),"",F{row}*G{row})'},
        )
        nombre = f"lecturas_{slugify(periodo.condominio.nombre) or 'condominio'}_{periodo.anio}_{periodo.mes:02d}.xlsx"
    else:
        return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
    response = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response


@login_required
def descargar_lecturas_cargadas(request, periodo_id):
    usuario = perfil(request)
    periodo = get_object_or_404(Periodo.objects.select_related("condominio"), pk=periodo_id)
    if usuario.rol not in ROLES_GESTION or not usuario.puede_acceder_condominio(periodo.condominio_id):
        messages.error(request, "No tienes permiso para descargar estas lecturas.")
        return redirect("gastos_comunes:lista")
    lecturas = periodo.lecturas.select_related("unidad", "unidad__edificio").order_by(
        "unidad__edificio__numero", "unidad__numero_unidad", "tipo"
    )
    filas = [[
        lectura.unidad.edificio.nombre, lectura.unidad.numero_unidad,
        lectura.get_tipo_display(), float(lectura.lectura_anterior),
        float(lectura.lectura_actual), float(lectura.consumo),
        float(lectura.tarifa), float(lectura.monto),
    ] for lectura in lecturas]
    contenido = crear_plantilla(
        "Lecturas importadas",
        f"Detalle de lecturas guardadas para {periodo.condominio.nombre}, período {periodo.mes:02d}/{periodo.anio}.",
        ["Edificio", "Unidad", "Medidor", "Lectura anterior", "Lectura actual", "Consumo", "Tarifa ($/unidad)", "Cargo ($)"],
        filas, [22, 16, 14, 19, 19, 16, 20, 18],
    )
    nombre = f"lecturas_cargadas_{slugify(periodo.condominio.nombre) or 'condominio'}_{periodo.anio}_{periodo.mes:02d}.xlsx"
    response = HttpResponse(contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return response


def _filas_csv(request):
    archivo = request.FILES.get("archivo")
    if not archivo:
        raise ValidationError("Selecciona una planilla Excel (.xlsx) o CSV.")
    if archivo.size > 5 * 1024 * 1024:
        raise ValidationError("El archivo supera el límite de 5 MB.")
    return archivo


@transaction.atomic
def importar_alicuotas(request, periodo):
    requeridas = {"edificio", "unidad", "alicuota"}
    filas = leer_tabla(_filas_csv(request), requeridas)
    por_clave = {(u.edificio.nombre, u.numero_unidad): u for u in Unidad.objects.filter(edificio__condominio=periodo.condominio, edificio__activo=True, activo=True).select_related("edificio")}
    vistas = set()
    for n, fila in enumerate(filas, start=2):
        if not any(str(valor or "").strip() for valor in fila.values()):
            continue
        if any(not str(fila.get(campo) or "").strip() for campo in requeridas):
            raise ValidationError(f"Fila {n}: faltan columnas o valores requeridos.")
        clave = (str(fila["edificio"]).strip(), str(fila["unidad"]).strip())
        unidad = por_clave.get(clave)
        if not unidad:
            raise ValidationError(f"Fila {n}: unidad/edificio no reconocidos ({clave[0]} / {clave[1]}).")
        if unidad.pk in vistas:
            raise ValidationError(f"Fila {n}: unidad duplicada.")
        vistas.add(unidad.pk)
        valor = Decimal(str(fila["alicuota"]).strip().replace(",", "."))
        if valor <= 0 or valor > 100:
            raise ValidationError(f"Fila {n}: la alícuota debe ser mayor que 0 y hasta 100.")
        unidad.alicuota = valor
        unidad.save(update_fields=["alicuota"])


@transaction.atomic
def importar_lecturas(request, periodo):
    requeridas = {"edificio", "unidad", "tipo", "anterior", "actual", "tarifa"}
    filas = leer_tabla(_filas_csv(request), requeridas)
    unidades = {(u.edificio.nombre, u.numero_unidad): u for u in Unidad.objects.filter(edificio__condominio=periodo.condominio, edificio__activo=True, activo=True).select_related("edificio")}
    cuenta = 0
    for n, fila in enumerate(filas, start=2):
        if not any(str(valor or "").strip() for valor in fila.values()):
            continue
        campos_lectura = {"anterior", "actual", "tarifa"}
        if all(not str(fila.get(campo) or "").strip() for campo in campos_lectura):
            continue
        if any(not str(fila.get(campo) or "").strip() for campo in requeridas):
            raise ValidationError(f"Fila {n}: faltan columnas o valores requeridos.")
        clave = (str(fila["edificio"]).strip(), str(fila["unidad"]).strip())
        unidad = unidades.get(clave)
        if not unidad:
            raise ValidationError(f"Fila {n}: unidad/edificio no reconocidos.")
        tipo = str(fila["tipo"]).strip().lower()
        tipo = {"agua": "agua", "gas": "gas", "otro": "otro"}.get(tipo)
        if not tipo:
            raise ValidationError(f"Fila {n}: tipo debe ser agua, gas u otro.")
        lectura = LecturaMedidor.objects.filter(periodo=periodo, unidad=unidad, tipo=tipo).first() or LecturaMedidor(periodo=periodo, unidad=unidad, tipo=tipo)
        lectura.lectura_anterior = Decimal(str(fila["anterior"]).strip().replace(",", "."))
        lectura.lectura_actual = Decimal(str(fila["actual"]).strip().replace(",", "."))
        lectura.tarifa = Decimal(str(fila["tarifa"]).strip().replace(",", "."))
        lectura.full_clean()
        lectura.save()
        cuenta += 1
    return cuenta

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
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
from .models import ItemGasto, LecturaMedidor, Liquidacion, PagoOnline, PagoRegistrado, Periodo
from .services import calcular_periodo, publicar_periodo, saldos_acumulados
from .excel import crear_plantilla, leer_tabla
from .mercadopago import MercadoPagoError, crear_orden, sincronizar_pago, verificar_firma_webhook

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
        periodos = Periodo.objects.filter(condominio__in=condominios_permitidos(usuario)).select_related("condominio")
        return render(request, "gastos_comunes/lista.html", {"usuario": usuario, "periodos": periodos, "es_gestion": True})
    unidades = Unidad.objects.filter(dueno=usuario) | Unidad.objects.filter(arrendatario=usuario)
    liquidaciones = Liquidacion.objects.filter(unidad__in=unidades, periodo__estado=Periodo.Estado.PUBLICADO).select_related("periodo", "unidad", "unidad__edificio", "periodo__condominio").prefetch_related("pagos")
    liquidaciones = list(liquidaciones.order_by("-periodo__anio", "-periodo__mes"))
    for liquidacion in liquidaciones:
        liquidacion.saldo_actual = saldos_acumulados(liquidacion.periodo.condominio, liquidacion.periodo).get(liquidacion.unidad_id, Decimal("0"))
        liquidacion.en_mora = liquidacion.saldo_actual > 0 and (liquidacion.saldo_anterior > 0 or liquidacion.periodo.vencimiento < timezone.localdate())
    return render(request, "gastos_comunes/lista.html", {"usuario": usuario, "liquidaciones": liquidaciones, "es_gestion": False})


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
        for liquidacion in liquidaciones:
            liquidacion.saldo_actual = saldos.get(liquidacion.unidad_id, Decimal("0"))
            liquidacion.en_mora = liquidacion.saldo_actual > 0 and (liquidacion.saldo_anterior > 0 or periodo.vencimiento < timezone.localdate())
        ultimo_periodo = Periodo.objects.filter(
            condominio=periodo.condominio, estado=Periodo.Estado.PUBLICADO
        ).order_by("-anio", "-mes").first()
        liquidacion_ids = [liquidacion.id for liquidacion in liquidaciones]
        pagos_online = PagoOnline.objects.filter(
            liquidacion_id__in=liquidacion_ids, usuario=usuario
        ).order_by("-creado")[:10]
        token_prueba = bool(getattr(settings, "MERCADOPAGO_TEST_ACCESS_TOKEN", ""))
        return render(request, "gastos_comunes/residente_detalle.html", {
            "periodo": periodo,
            "liquidaciones": liquidaciones,
            "es_ultimo_periodo": periodo == ultimo_periodo,
            "puede_pagar_en_linea": periodo == ultimo_periodo and token_prueba,
            "pagos_online_configurados": token_prueba,
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
                return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
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
        elif accion == "lecturas" and periodo.estado == Periodo.Estado.BORRADOR:
            try:
                cantidad = importar_lecturas(request, periodo)
                messages.success(request, f"Se importaron {cantidad} lecturas.")
                return redirect("gastos_comunes:detalle", periodo_id=periodo.pk)
            except (ValueError, ValidationError, KeyError, InvalidOperation, UnicodeDecodeError) as exc:
                messages.error(request, f"No se importaron las lecturas: {error_formulario(exc)}")

    liquidaciones = list(periodo.liquidaciones.select_related("unidad", "unidad__edificio").prefetch_related("pagos", "detalles").order_by("unidad__edificio__numero", "unidad__numero_unidad"))
    saldos = saldos_acumulados(periodo.condominio, periodo) if periodo.estado == Periodo.Estado.PUBLICADO else {}
    grupos_por_edificio = {}
    for liq in liquidaciones:
        liq.pagado_calculado = sum((p.monto for p in liq.pagos.all()), Decimal("0"))
        liq.saldo_actual = saldos.get(liq.unidad_id, Decimal("0"))
        liq.en_mora = liq.saldo_actual > 0 and (liq.saldo_anterior > 0 or periodo.vencimiento < timezone.localdate())
        grupo = grupos_por_edificio.setdefault(liq.unidad.edificio_id, {
            "id": liq.unidad.edificio_id, "nombre": liq.unidad.edificio.nombre,
            "unidades": [], "total": Decimal("0"),
        })
        grupo["unidades"].append(liq)
        grupo["total"] += liq.total
    grupos_liquidacion = list(grupos_por_edificio.values())
    gastos = list(periodo.gastos.select_related("edificio", "unidad"))
    lecturas = list(periodo.lecturas.select_related("unidad", "unidad__edificio").order_by("unidad__edificio__numero", "unidad__numero_unidad", "tipo"))
    ultimo_periodo = Periodo.objects.filter(condominio=periodo.condominio, estado=Periodo.Estado.PUBLICADO).order_by("-anio", "-mes").first()
    return render(request, "gastos_comunes/periodo_detalle.html", {
        "periodo": periodo, "gastos": gastos,
        "lecturas": lecturas, "liquidaciones": liquidaciones,
        "grupos_liquidacion": grupos_liquidacion,
        "total_liquidaciones": sum((liq.total for liq in liquidaciones), Decimal("0")),
        "item_form": item_form, "pago_form": pago_form,
        "es_gestion": True, "unidades_sin_alicuota": Unidad.objects.filter(edificio__condominio=periodo.condominio, alicuota__isnull=True).count(),
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
    if not getattr(settings, "MERCADOPAGO_TEST_ACCESS_TOKEN", ""):
        messages.error(request, "El pago de prueba todavía no está configurado.")
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
    ).filter(
        Q(estado__in=[PagoOnline.Estado.CREADO, PagoOnline.Estado.PENDIENTE])
        | Q(estado=PagoOnline.Estado.ERROR, orden_proveedor__isnull=True)
    ).order_by("-creado").first()
    if intento_abierto:
        if intento_abierto.monto != monto:
            messages.error(request, "Ya hay un pago en línea pendiente para esta unidad. Revisa su estado antes de iniciar otro.")
            return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
        if intento_abierto.url_pago:
            return redirect(intento_abierto.url_pago)
        pago = intento_abierto
    else:
        pago = PagoOnline.objects.create(liquidacion=liquidacion, usuario=usuario, monto=monto)

    resultado_url = request.build_absolute_uri(
        reverse("gastos_comunes:pago_resultado", kwargs={"pago_id": pago.referencia_externa})
    )
    try:
        checkout_url = crear_orden(pago, {
            "success": resultado_url,
            "failure": resultado_url,
            "pending": resultado_url,
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
    if pago.orden_proveedor and pago.estado != PagoOnline.Estado.APROBADO:
        try:
            # El regreso del checkout por sí solo nunca confirma un pago.
            pago = sincronizar_pago(pago)
        except MercadoPagoError:
            pass
    return render(request, "gastos_comunes/pago_resultado.html", {"pago": pago})


@csrf_exempt
@require_POST
def mercadopago_webhook(request):
    secret = getattr(settings, "MERCADOPAGO_TEST_WEBHOOK_SECRET", "")
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
    data = payload.get("data")
    order_id = request.GET.get("data.id") or (data.get("id") if isinstance(data, dict) else None)
    if not order_id:
        return HttpResponseBadRequest("Falta el identificador de la orden.")
    pago = PagoOnline.objects.filter(orden_proveedor=str(order_id)).first()
    if not pago:
        return HttpResponse(status=200)
    try:
        sincronizar_pago(pago)
    except MercadoPagoError:
        return HttpResponse(status=503)
    return HttpResponse(status=200)


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
    unidades = list(Unidad.objects.filter(edificio__condominio=periodo.condominio).select_related("edificio").order_by("edificio__numero", "numero_unidad"))
    if tipo == "alicuotas":
        filas = [[u.edificio.nombre, u.numero_unidad, ""] for u in unidades]
        contenido = crear_plantilla(
            "Alícuotas oficiales",
            "Complete la columna amarilla con el porcentaje indicado en el reglamento. Ejemplo: escriba 0,58 para representar 0,58%. No cambie los identificadores de edificio ni unidad.",
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
    por_clave = {(u.edificio.nombre, u.numero_unidad): u for u in Unidad.objects.filter(edificio__condominio=periodo.condominio).select_related("edificio")}
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
    unidades = {(u.edificio.nombre, u.numero_unidad): u for u in Unidad.objects.filter(edificio__condominio=periodo.condominio).select_related("edificio")}
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

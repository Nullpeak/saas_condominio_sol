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
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, HttpResponseServerError, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import Condominio, Unidad, Usuario
from .forms import ItemGastoForm, PagoForm, PeriodoForm
from .models import ItemGasto, LecturaMedidor, Liquidacion, MercadoPagoConexion, MercadoPagoIntentoOAuth, PagoOnline, PagoRegistrado, Periodo, WebpayConexion
from .services import calcular_periodo, movimientos_cartola, publicar_periodo, resumen_mora, saldos_acumulados
from .excel import crear_plantilla, leer_tabla
from .mercadopago import MercadoPagoError, crear_checkout, crear_intento_oauth, completar_oauth, es_modo_prueba, localizar_pago_webhook, secreto_webhook_actual, sincronizar_pago, verificar_firma_webhook
from .webpay import WebpayError, actualizar_estado_pago as actualizar_estado_webpay, cifrar_api_key, consultar_transaccion as consultar_webpay, confirmar_transaccion as confirmar_webpay, crear_transaccion as crear_transaccion_webpay, validar_api_key, validar_codigo_comercio

ROLES_GESTION = {Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR, Usuario.Rol.GESTOR}
MESES_NOMBRE = [
    (1, "Enero"), (2, "Febrero"), (3, "Marzo"), (4, "Abril"),
    (5, "Mayo"), (6, "Junio"), (7, "Julio"), (8, "Agosto"),
    (9, "Septiembre"), (10, "Octubre"), (11, "Noviembre"), (12, "Diciembre"),
]


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
        condominios = condominios_permitidos(usuario).order_by("nombre")
        periodos_todos = Periodo.objects.filter(condominio__in=condominios)
        periodos = periodos_todos.select_related("condominio").order_by(
            "-anio", "-mes", "condominio__nombre", "-pk"
        )
        filtro_condominio = request.GET.get("condominio", "").strip()
        filtro_anio = request.GET.get("anio", "").strip()
        filtro_mes = request.GET.get("mes", "").strip()
        filtro_estado = request.GET.get("estado", "").strip()
        if filtro_condominio.isdigit():
            periodos = periodos.filter(condominio_id=filtro_condominio)
        if filtro_anio.isdigit() and 2000 <= int(filtro_anio) <= 2100:
            periodos = periodos.filter(anio=int(filtro_anio))
        if filtro_mes.isdigit() and 1 <= int(filtro_mes) <= 12:
            periodos = periodos.filter(mes=int(filtro_mes))
        if filtro_estado in Periodo.Estado.values:
            periodos = periodos.filter(estado=filtro_estado)

        paginacion = paginar(request, periodos, por_pagina=20)
        return render(request, "gastos_comunes/lista.html", {
            "usuario": usuario,
            "periodos": paginacion["page_obj"],
            "es_gestion": True,
            "puede_configurar_webpay": usuario.rol in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER),
            "condominios_filtro": condominios,
            "filtro_condominio": filtro_condominio,
            "filtro_anio": filtro_anio,
            "filtro_mes": filtro_mes,
            "filtro_estado": filtro_estado,
            "meses": MESES_NOMBRE,
            "total_periodos": periodos_todos.count(),
            "periodos_publicados": periodos_todos.filter(estado=Periodo.Estado.PUBLICADO).count(),
            "periodos_borrador": periodos_todos.filter(estado=Periodo.Estado.BORRADOR).count(),
            "filtros_activos": bool(filtro_condominio or filtro_anio or filtro_mes or filtro_estado),
            **paginacion,
        })
    unidades = Unidad.objects.filter(Q(dueno=usuario) | Q(arrendatario=usuario)).select_related(
        "edificio__condominio"
    ).distinct().order_by("edificio__condominio__nombre", "edificio__numero", "numero_unidad")
    unidad_param = request.GET.get("unidad", "").strip()
    if unidad_param.isdigit():
        unidades = unidades.filter(pk=unidad_param)

    resumen_unidades = []
    movimientos = []
    for unidad in unidades:
        cartola = movimientos_cartola(unidad)
        movimientos.extend(cartola["movimientos"])
        ultimo_periodo = Periodo.objects.filter(
            condominio=unidad.edificio.condominio, estado=Periodo.Estado.PUBLICADO
        ).order_by("-anio", "-mes").first()
        saldo = saldos_acumulados(unidad.edificio.condominio, ultimo_periodo).get(
            unidad.pk, Decimal("0")
        ) if ultimo_periodo else Decimal("0")
        ultima_liquidacion = Liquidacion.objects.filter(
            unidad=unidad, periodo__estado=Periodo.Estado.PUBLICADO
        ).select_related("periodo").order_by("-periodo__anio", "-periodo__mes").first()
        conexion = WebpayConexion.objects.filter(condominio=unidad.edificio.condominio).first()
        resumen_unidades.append({
            "unidad": unidad,
            "saldo": saldo,
            "ultima_liquidacion": ultima_liquidacion,
            "webpay_disponible": bool(conexion and ultima_liquidacion and saldo > 0),
            "modo_prueba": conexion.modo_prueba if conexion else True,
        })
    movimientos.sort(key=lambda e: (e["fecha"], e["orden"]), reverse=True)
    paginacion = paginar(request, movimientos, por_pagina=20)
    return render(request, "gastos_comunes/lista.html", {
        "usuario": usuario,
        "es_gestion": False,
        "unidades_resumen": resumen_unidades,
        "saldo_total": sum((u["saldo"] for u in resumen_unidades), Decimal("0")),
        "filtro_unidad": unidad_param,
        "opciones_unidad": Unidad.objects.filter(
            Q(dueno=usuario) | Q(arrendatario=usuario)
        ).select_related("edificio", "edificio__condominio").distinct().order_by(
            "edificio__condominio__nombre", "edificio__numero", "numero_unidad"
        ),
        "movimientos": paginacion["page_obj"],
        **paginacion,
    })


@login_required
def cartola_unidad(request, unidad_id):
    usuario = perfil(request)
    unidad = get_object_or_404(
        Unidad.objects.select_related("edificio__condominio", "dueno", "arrendatario"),
        pk=unidad_id,
    )
    es_gestion = usuario.rol in ROLES_GESTION
    if es_gestion:
        if not usuario.puede_acceder_condominio(unidad.edificio.condominio_id):
            return HttpResponseForbidden("No tienes acceso a la cartola de este condominio.")
    elif usuario.rol != Usuario.Rol.RESIDENTE or usuario.pk not in (unidad.dueno_id, unidad.arrendatario_id):
        return HttpResponseForbidden("No tienes acceso a la cartola de esta unidad.")

    cartola = movimientos_cartola(unidad)
    movimientos_paginados = paginar(
        request, cartola["movimientos"], por_pagina=20, parametro="movimientos_page"
    )
    cartola["movimientos"] = movimientos_paginados["page_obj"]
    ultimo_periodo = Periodo.objects.filter(
        condominio=unidad.edificio.condominio, estado=Periodo.Estado.PUBLICADO
    ).order_by("-anio", "-mes").first()
    saldo = saldos_acumulados(unidad.edificio.condominio, ultimo_periodo).get(
        unidad.pk, Decimal("0")
    ) if ultimo_periodo else Decimal("0")
    ultima_liquidacion = Liquidacion.objects.filter(
        unidad=unidad, periodo__estado=Periodo.Estado.PUBLICADO
    ).select_related("periodo").order_by("-periodo__anio", "-periodo__mes").first()
    conexion = WebpayConexion.objects.filter(condominio=unidad.edificio.condominio).first()
    intentos_online = PagoOnline.objects.filter(
        liquidacion__unidad=unidad
    ).select_related("liquidacion__periodo", "usuario")
    if not es_gestion:
        intentos_online = intentos_online.filter(usuario=usuario)
    pagos_paginados = paginar(
        request, intentos_online.order_by("-creado"), por_pagina=20,
        parametro="intentos_page",
    )
    return render(request, "gastos_comunes/cartola_unidad.html", {
        "unidad": unidad,
        "cartola": cartola,
        "saldo": saldo,
        "ultima_liquidacion": ultima_liquidacion,
        "es_gestion": es_gestion,
        "puede_pagar": bool(
            not es_gestion and saldo > 0 and ultima_liquidacion and conexion
        ),
        "webpay_modo_prueba": conexion.modo_prueba if conexion else True,
        "pagos_online": pagos_paginados["page_obj"],
        "movimientos_page_obj": movimientos_paginados["page_obj"],
        "movimientos_page_range": movimientos_paginados["page_range"],
        "movimientos_querystring": movimientos_paginados["querystring"],
        "intentos_page_obj": pagos_paginados["page_obj"],
        "intentos_page_range": pagos_paginados["page_range"],
        "intentos_querystring": pagos_paginados["querystring"],
        "hoy": timezone.localdate(),
    })


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
        unidades_residente = Unidad.objects.filter(Q(dueno=usuario) | Q(arrendatario=usuario)).distinct()
        unidad_param = request.GET.get("unidad", "").strip()
        unidad_seleccionada = None
        if unidad_param:
            if not unidad_param.isdigit():
                return HttpResponseForbidden("La unidad indicada no es válida.")
            unidad_seleccionada = unidades_residente.filter(
                pk=unidad_param, edificio__condominio=periodo.condominio
            ).first()
            if unidad_seleccionada is None:
                return HttpResponseForbidden("No tienes acceso a esa unidad.")
            unidades_residente = unidades_residente.filter(pk=unidad_seleccionada.pk)
        unidad_ids = unidades_residente.values_list("id", flat=True)
        liquidaciones = Liquidacion.objects.filter(periodo=periodo, unidad_id__in=unidad_ids, periodo__estado=Periodo.Estado.PUBLICADO).prefetch_related("detalles", "pagos")
        liquidaciones = list(liquidaciones)
        cartolas_por_unidad = {}
        saldos = saldos_acumulados(periodo.condominio, periodo)
        moras = resumen_mora(periodo.condominio)
        for liquidacion in liquidaciones:
            if liquidacion.unidad_id not in cartolas_por_unidad:
                cartolas_por_unidad[liquidacion.unidad_id] = movimientos_cartola(liquidacion.unidad)
            movimiento_boleta = next(
                (m for m in cartolas_por_unidad[liquidacion.unidad_id]["movimientos"]
                 if m["liquidacion"].pk == liquidacion.pk),
                None,
            )
            liquidacion.abonos_aplicados = movimiento_boleta["abonos"] if movimiento_boleta else []
            liquidacion.total_abonado = movimiento_boleta["monto_pagado"] if movimiento_boleta else Decimal("0")
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
        conexion_webpay = WebpayConexion.objects.filter(condominio=periodo.condominio).first()
        cuenta_conectada = conexion_webpay is not None
        return render(request, "gastos_comunes/residente_detalle.html", {
            "periodo": periodo,
            "liquidaciones": liquidaciones,
            "unidad_seleccionada": unidad_seleccionada,
            "es_ultimo_periodo": periodo == ultimo_periodo,
            "webpay_modo_prueba": conexion_webpay.modo_prueba if conexion_webpay else True,
            "puede_pagar_en_linea": periodo == ultimo_periodo and cuenta_conectada,
            "pagos_online_configurados": cuenta_conectada,
            "pagos_online": pagos_online,
        })

    item_form = ItemGastoForm(periodo=periodo)
    pago_form = PagoForm()
    pago_unidad_seleccionada = None
    saldo_unidad_pago = Decimal("0")
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
            liquidacion = get_object_or_404(
                Liquidacion.objects.select_related("unidad__edificio"),
                pk=request.POST.get("liquidacion_id"), periodo=periodo,
                unidad__activo=True, unidad__edificio__activo=True,
            )
            pago_unidad_seleccionada = liquidacion
            pago_form = PagoForm(request.POST)
            ultimo_periodo = Periodo.objects.filter(
                condominio=periodo.condominio,
                estado=Periodo.Estado.PUBLICADO,
            ).order_by("-anio", "-mes").first()
            saldos = saldos_acumulados(periodo.condominio, ultimo_periodo) if ultimo_periodo else {}
            saldo_unidad_pago = saldos.get(liquidacion.unidad_id, Decimal("0"))
            if pago_form.is_valid():
                pago = pago_form.save(commit=False)
                pago.liquidacion = liquidacion
                pago.registrado_por = usuario
                if ultimo_periodo != periodo:
                    pago_form.add_error(None, "Registra los pagos desde el período publicado más reciente para mantener correcto el saldo acumulado.")
                elif saldo_unidad_pago <= 0:
                    pago_form.add_error(None, "Esta unidad ya no tiene saldo pendiente.")
                elif pago.monto > saldo_unidad_pago:
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
    total_pagado_periodo = Decimal("0")
    total_saldo_acumulado = Decimal("0")
    unidades_con_saldo = 0
    unidades_vencidas = 0
    for liq in liquidaciones:
        liq.pagado_calculado = sum((p.monto for p in liq.pagos.all()), Decimal("0"))
        liq.saldo_actual = saldos.get(liq.unidad_id, Decimal("0"))
        mora = moras.get(liq.unidad_id, {})
        liq.en_mora = liq.saldo_actual > 0 and (
            mora.get("principal_vencido", Decimal("0")) > 0
            or mora.get("interes_devengado", Decimal("0")) > 0
        )
        liq.estado_cuenta = "vencido" if liq.saldo_actual > 0 and liq.en_mora else "pendiente" if liq.saldo_actual > 0 else "al_dia"
        total_pagado_periodo += liq.pagado_calculado
        total_saldo_acumulado += liq.saldo_actual
        unidades_con_saldo += int(liq.saldo_actual > 0)
        unidades_vencidas += int(liq.estado_cuenta == "vencido")
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
        "total_pagado_periodo": total_pagado_periodo,
        "total_saldo_acumulado": total_saldo_acumulado,
        "unidades_con_saldo": unidades_con_saldo,
        "unidades_vencidas": unidades_vencidas,
        "item_form": item_form, "pago_form": pago_form,
        "es_gestion": True, "unidades_sin_alicuota": Unidad.objects.filter(edificio__condominio=periodo.condominio, edificio__activo=True, activo=True, alicuota__isnull=True).count(),
        "unidades_activas_count": len(unidades_activas),
        "alicuotas_configuradas": alicuotas_configuradas,
        "suma_alicuotas": suma_alicuotas,
        "today": timezone.localdate(),
        "puede_registrar_pagos": ultimo_periodo == periodo,
        "pago_unidad_seleccionada": pago_unidad_seleccionada,
        "saldo_unidad_pago": saldo_unidad_pago,
    })


@login_required
@require_GET
def buscar_unidades_pago(request, periodo_id):
    """Busca unidades con deuda del condominio/período actual para registrar un abono."""
    usuario = perfil(request)
    if usuario.rol not in ROLES_GESTION:
        return JsonResponse({"results": []}, status=403)

    periodo = get_object_or_404(
        Periodo.objects.select_related("condominio"),
        pk=periodo_id,
        estado=Periodo.Estado.PUBLICADO,
    )
    if not usuario.puede_acceder_condominio(periodo.condominio_id):
        return JsonResponse({"results": []}, status=403)

    ultimo_periodo = Periodo.objects.filter(
        condominio=periodo.condominio,
        estado=Periodo.Estado.PUBLICADO,
    ).order_by("-anio", "-mes").first()
    if ultimo_periodo != periodo:
        return JsonResponse({"results": [], "error": "Registra pagos desde el período publicado más reciente."}, status=409)

    consulta = request.GET.get("q", "").strip()
    if len(consulta) < 2:
        return JsonResponse({"results": []})

    coincidencia = (
        Q(unidad__numero_unidad__icontains=consulta)
        | Q(unidad__edificio__nombre__icontains=consulta)
        | Q(unidad__dueno__nombre__icontains=consulta)
        | Q(unidad__dueno__nombre2__icontains=consulta)
        | Q(unidad__dueno__apellido__icontains=consulta)
        | Q(unidad__dueno__apellido2__icontains=consulta)
        | Q(unidad__dueno__rut__icontains=consulta)
        | Q(unidad__arrendatario__nombre__icontains=consulta)
        | Q(unidad__arrendatario__nombre2__icontains=consulta)
        | Q(unidad__arrendatario__apellido__icontains=consulta)
        | Q(unidad__arrendatario__apellido2__icontains=consulta)
        | Q(unidad__arrendatario__rut__icontains=consulta)
    )
    if consulta.isdigit():
        coincidencia |= Q(unidad__edificio__numero=consulta)

    liquidaciones_coincidentes = Liquidacion.objects.filter(
        periodo=periodo,
        unidad__activo=True,
        unidad__edificio__activo=True,
    ).filter(coincidencia).select_related(
        "unidad__edificio", "unidad__dueno", "unidad__arrendatario"
    ).order_by("unidad__edificio__numero", "unidad__numero_unidad")
    saldos = saldos_acumulados(periodo.condominio, periodo)
    resultados = []
    for liquidacion in liquidaciones_coincidentes.iterator(chunk_size=100):
        saldo = saldos.get(liquidacion.unidad_id, Decimal("0"))
        if saldo <= 0:
            continue
        unidad = liquidacion.unidad
        residentes = []
        for etiqueta, residente in (("Dueño", unidad.dueno), ("Arrendatario", unidad.arrendatario)):
            if residente:
                nombre = " ".join(
                    parte for parte in (
                        residente.nombre, residente.nombre2,
                        residente.apellido, residente.apellido2,
                    ) if parte
                )
                rut = (residente.rut or "").strip()
                detalle_residente = f"{etiqueta}: {nombre}"
                if rut:
                    detalle_residente += f" · RUT: {rut}"
                if not any(nombre == existente for existente in residentes):
                    residentes.append(detalle_residente)
        resultados.append({
            "liquidacion_id": liquidacion.pk,
            "unidad_id": unidad.pk,
            "etiqueta": f"{unidad.edificio.nombre} · Unidad {unidad.numero_unidad}",
            "residentes": " · ".join(residentes),
            "saldo": str(saldo),
            "cartola_url": reverse("gastos_comunes:cartola_unidad", args=[unidad.pk]),
        })
        if len(resultados) == 10:
            break

    return JsonResponse({"results": resultados, "limit": 10})


@login_required
@require_POST
def iniciar_pago_online(request, liquidacion_id):
    usuario = perfil(request)
    if usuario.rol != Usuario.Rol.RESIDENTE:
        messages.error(request, "Los pagos en línea están disponibles solo para residentes.")
        return redirect("gastos_comunes:lista")
    liquidacion = get_object_or_404(
        Liquidacion.objects.select_related("periodo__condominio", "unidad__edificio"),
        pk=liquidacion_id,
        periodo__estado=Periodo.Estado.PUBLICADO,
    )
    if liquidacion.unidad.dueno_id != usuario.pk and liquidacion.unidad.arrendatario_id != usuario.pk:
        return HttpResponseForbidden("No tienes permiso para pagar esta unidad.")
    conexion = WebpayConexion.objects.filter(condominio=liquidacion.periodo.condominio).first()
    if not conexion:
        messages.error(request, "El condominio todavía no tiene Webpay Plus configurado por su administrador.")
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
        proveedor=PagoOnline.Proveedor.WEBPAY,
        estado__in=[PagoOnline.Estado.CREADO, PagoOnline.Estado.PENDIENTE],
    ).order_by("-creado").first()
    if intento_abierto:
        if intento_abierto.usuario_id != usuario.pk:
            messages.info(request, "Ya hay un intento de pago abierto para esta liquidación. Espera su resultado antes de iniciar otro.")
            return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
        if intento_abierto.monto != monto:
            messages.error(request, "Ya hay un pago en línea pendiente para esta unidad. Revisa su estado antes de iniciar otro.")
            return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
        # Reutilizar una orden únicamente mientras siga abierta.
        if intento_abierto.url_pago and intento_abierto.token_webpay:
            return render(request, "gastos_comunes/webpay_redirect.html", {
                "url_webpay": intento_abierto.url_pago,
                "token_webpay": intento_abierto.token_webpay,
            })
        if not intento_abierto.token_webpay:
            pago = intento_abierto
        else:
            pago = PagoOnline.objects.create(liquidacion=liquidacion, usuario=usuario, monto=monto, proveedor=PagoOnline.Proveedor.WEBPAY)
    else:
        pago = PagoOnline.objects.create(liquidacion=liquidacion, usuario=usuario, monto=monto, proveedor=PagoOnline.Proveedor.WEBPAY)

    sitio_url = getattr(settings, "SITIO_URL", "")
    if not sitio_url.startswith("https://"):
        pago.estado = PagoOnline.Estado.ERROR
        pago.detalle_estado = "https_return_url_not_configured"
        pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        messages.error(request, "Webpay requiere configurar SITIO_URL con la dirección HTTPS pública del sitio, por ejemplo la URL actual de ngrok.")
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
    return_url = sitio_url + reverse("gastos_comunes:webpay_retorno", kwargs={"pago_id": pago.referencia_externa})
    try:
        pago.orden_proveedor = f"GDC{pago.referencia_externa.hex[:23]}"
        token, checkout_url = crear_transaccion_webpay(conexion, pago, return_url)
        pago.token_webpay = token
        pago.url_pago = checkout_url
        pago.estado = PagoOnline.Estado.PENDIENTE
        pago.detalle_estado = "webpay_checkout_created"
        pago.save(update_fields=["orden_proveedor", "token_webpay", "url_pago", "estado", "detalle_estado", "actualizado"])
    except WebpayError as exc:
        pago.estado = PagoOnline.Estado.ERROR
        pago.detalle_estado = str(exc)[:100]
        pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        messages.error(request, str(exc))
        return redirect("gastos_comunes:detalle", periodo_id=liquidacion.periodo_id)
    return render(request, "gastos_comunes/webpay_redirect.html", {"url_webpay": checkout_url, "token_webpay": token})


@login_required
def resultado_pago_online(request, pago_id):
    usuario = perfil(request)
    pago = get_object_or_404(
        PagoOnline.objects.select_related("liquidacion__periodo", "liquidacion__unidad"),
        referencia_externa=pago_id,
    )
    if pago.usuario_id != usuario.pk:
        return HttpResponseForbidden("No tienes permiso para ver este pago.")
    if pago.proveedor == PagoOnline.Proveedor.WEBPAY and pago.token_webpay and pago.estado == PagoOnline.Estado.PENDIENTE:
        conexion = WebpayConexion.objects.filter(condominio=pago.liquidacion.periodo.condominio).first()
        try:
            if conexion:
                estado = consultar_webpay(conexion, pago.token_webpay)
                if estado.get("status") == "AUTHORIZED":
                    estado = confirmar_webpay(conexion, pago.token_webpay)
                pago = actualizar_estado_webpay(pago, estado)
        except WebpayError:
            pass
    elif pago.proveedor == PagoOnline.Proveedor.MERCADOPAGO:
        payment_id = request.GET.get("payment_id") or request.GET.get("collection_id")
        order_id = request.GET.get("order_id")
        if (payment_id or order_id or pago.orden_proveedor) and pago.estado != PagoOnline.Estado.APROBADO:
            try:
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
def webpay_retorno(request, pago_id):
    """Retorno firmado operacionalmente mediante el token opaco de Transbank."""
    if request.method not in ("GET", "POST"):
        return HttpResponseBadRequest("Método no permitido.")
    pago = get_object_or_404(
        PagoOnline.objects.select_related("liquidacion__periodo__condominio"),
        referencia_externa=pago_id,
        proveedor=PagoOnline.Proveedor.WEBPAY,
    )
    datos = request.POST if request.method == "POST" else request.GET
    token = datos.get("token_ws", "")
    token_abortado = datos.get("TBK_TOKEN", "")
    orden = datos.get("TBK_ORDEN_COMPRA", "")
    sesion = datos.get("TBK_ID_SESION", "")
    if orden and orden != pago.orden_proveedor:
        return HttpResponseForbidden("La orden recibida no corresponde al pago.")
    if sesion and sesion != str(pago.referencia_externa):
        return HttpResponseForbidden("La sesión recibida no corresponde al pago.")
    if token and token != pago.token_webpay:
        return HttpResponseForbidden("El token recibido no corresponde al pago.")
    if token_abortado and token_abortado != pago.token_webpay:
        return HttpResponseForbidden("El token recibido no corresponde al pago.")
    # Webpay regresa desde un sitio externo y algunos navegadores no envían la
    # cookie de sesión original en ese retorno. Solo permitimos mostrar el
    # resultado desde esta ruta después de validar el token/orden de Transbank.
    if pago.estado == PagoOnline.Estado.APROBADO:
        if not (token or token_abortado or (orden and sesion)):
            return HttpResponseForbidden("Faltan datos para validar el retorno del pago.")
        return render(request, "gastos_comunes/pago_resultado.html", {"pago": pago})
    conexion = WebpayConexion.objects.filter(condominio=pago.liquidacion.periodo.condominio).first()
    if not conexion:
        return HttpResponseServerError("La conexión Webpay del condominio ya no está disponible.")
    try:
        if token:
            resultado = confirmar_webpay(conexion, token)
            pago = actualizar_estado_webpay(pago, resultado)
        elif token_abortado:
            pago.estado = PagoOnline.Estado.CANCELADO
            pago.detalle_estado = "webpay_buyer_cancelled"
            pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        elif orden and pago.token_webpay:
            # En timeout no llega token de retorno. Consultar estado evita declarar
            # pagado un intento que solo quedó abierto en la sesión del comprador.
            resultado = consultar_webpay(conexion, pago.token_webpay)
            if resultado.get("status") == "AUTHORIZED":
                resultado = confirmar_webpay(conexion, pago.token_webpay)
                pago = actualizar_estado_webpay(pago, resultado)
            else:
                pago.estado = PagoOnline.Estado.CANCELADO
                pago.detalle_estado = "webpay_checkout_timeout"
                pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        else:
            pago.estado = PagoOnline.Estado.CANCELADO
            pago.detalle_estado = "webpay_checkout_incomplete"
            pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
    except WebpayError as exc:
        messages.error(request, f"No pudimos confirmar el pago con Transbank: {exc}")
    # Renderizar aquí evita que un retorno legítimo termine en /login/ cuando
    # Opera u otro navegador no conserva la sesión al volver desde Transbank.
    return render(request, "gastos_comunes/pago_resultado.html", {"pago": pago})


@csrf_exempt
@require_POST
def mercadopago_webhook(request):
    try:
        secret = secreto_webhook_actual()
    except MercadoPagoError:
        return HttpResponseServerError("Ambiente de Mercado Pago no configurado.")
    if not secret:
        return HttpResponseServerError("Webhook de Mercado Pago no configurado.")
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
def webpay_cuentas(request):
    usuario = perfil(request)
    if usuario.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER):
        return HttpResponseForbidden("Solo un administrador autorizado puede configurar Webpay.")
    condominios = list(condominios_permitidos(usuario).select_related("webpay_conexion").order_by("nombre"))
    return render(request, "gastos_comunes/webpay_cuentas.html", {
        "condominios": condominios,
    })


@login_required
@require_POST
def webpay_configurar(request, condominio_id):
    usuario = perfil(request)
    if usuario.rol not in (Usuario.Rol.ADMINISTRADOR, Usuario.Rol.SUPER) or not usuario.puede_acceder_condominio(condominio_id):
        return HttpResponseForbidden("No tienes permiso para gestionar este condominio.")
    condominio = get_object_or_404(Condominio, pk=condominio_id, activo=True)
    modo_prueba = request.POST.get("ambiente") == "prueba"
    if not modo_prueba and request.POST.get("ambiente") != "produccion":
        messages.error(request, "Selecciona un ambiente válido para Webpay.")
        return redirect("gastos_comunes:webpay_cuentas")

    conexion = WebpayConexion.objects.filter(condominio=condominio).first()
    codigo = request.POST.get("codigo_comercio", "").strip()
    api_key = request.POST.get("api_key", "").strip()
    if not modo_prueba:
        if not validar_codigo_comercio(codigo):
            messages.error(request, "El código de comercio debe contener solo números y tener entre 8 y 20 dígitos.")
            return redirect("gastos_comunes:webpay_cuentas")
        if not api_key and not (conexion and conexion.api_key_cifrada):
            messages.error(request, "Ingresa la llave API entregada por Transbank para este condominio.")
            return redirect("gastos_comunes:webpay_cuentas")
        if api_key and not validar_api_key(api_key):
            messages.error(request, "La llave API no tiene un formato válido.")
            return redirect("gastos_comunes:webpay_cuentas")
        try:
            api_key_cifrada = cifrar_api_key(api_key) if api_key else conexion.api_key_cifrada
        except WebpayError as exc:
            messages.error(request, str(exc))
            return redirect("gastos_comunes:webpay_cuentas")
    else:
        codigo = conexion.codigo_comercio if conexion else ""
        api_key_cifrada = conexion.api_key_cifrada if conexion else ""

    if conexion:
        conexion.codigo_comercio = codigo
        conexion.api_key_cifrada = api_key_cifrada
        conexion.modo_prueba = modo_prueba
        conexion.configurado_por = usuario
        conexion.save(update_fields=["codigo_comercio", "api_key_cifrada", "modo_prueba", "configurado_por", "actualizado"])
    else:
        WebpayConexion.objects.create(
            condominio=condominio,
            codigo_comercio=codigo,
            api_key_cifrada=api_key_cifrada,
            modo_prueba=modo_prueba,
            configurado_por=usuario,
        )
    messages.success(request, f"Webpay Plus quedó configurado para {condominio.nombre} en ambiente de {'prueba' if modo_prueba else 'producción'}.")
    return redirect("gastos_comunes:webpay_cuentas")


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

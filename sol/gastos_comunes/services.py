from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from core.models import Unidad
from .models import Periodo, Liquidacion, DetalleCargo, ItemGasto, LecturaMedidor, PagoRegistrado, TasaInteresCorriente

CENTAVO = Decimal("0.01")


def dinero(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def saldos_acumulados(condominio, hasta_periodo=None):
    """Saldo acumulado por unidad expresado en pesos enteros (CLP)."""
    qs = Liquidacion.objects.filter(periodo__condominio=condominio, periodo__estado=Periodo.Estado.PUBLICADO)
    if hasta_periodo:
        qs = qs.filter(periodo__anio__lte=hasta_periodo.anio).exclude(
            periodo__anio=hasta_periodo.anio, periodo__mes__gt=hasta_periodo.mes
        ).exclude(periodo__anio__gt=hasta_periodo.anio)
    filas = qs.values("unidad_id").annotate(
        ordinario=Sum("total_ordinario"), reserva=Sum("fondo_reserva"),
        extra=Sum("total_extraordinario"), consumos=Sum("consumos"),
        intereses=Sum("intereses"),
    )
    pagados = dict(PagoRegistrado.objects.filter(liquidacion__in=qs).values("liquidacion__unidad_id").annotate(total=Sum("monto")).values_list("liquidacion__unidad_id", "total"))
    saldos = {}
    for fila in filas:
        saldo = (
            (fila["ordinario"] or 0)
            + (fila["reserva"] or 0)
            + (fila["extra"] or 0)
            + (fila["consumos"] or 0)
            + (fila["intereses"] or 0)
            - pagados.get(fila["unidad_id"], Decimal("0"))
        )
        # Los pagos en CLP se cursan en pesos enteros. Redondear aquí evita
        # mostrar $0 y bloquear una reserva por una diferencia de centavos.
        saldos[fila["unidad_id"]] = Decimal(saldo).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    return saldos


def fecha_inicio_mora(periodo):
    """El atraso empieza después del vencimiento y del plazo legal de diez días."""
    fecha_emision = (periodo.publicado or periodo.creado).date()
    vencimiento = max(periodo.vencimiento, fecha_emision + timedelta(days=10))
    return vencimiento + timedelta(days=1)


def _interes_en_intervalo(capital, desde, hasta, condominio):
    """Interés simple diario según tasas CMF vigentes, sobre base anual 360."""
    if capital <= 0 or desde >= hasta:
        return Decimal("0")
    tasas = list(TasaInteresCorriente.objects.filter(
        vigente_desde__lt=hasta,
    ).filter(
        Q(vigente_hasta__isnull=True) | Q(vigente_hasta__gte=desde)
    ).order_by("vigente_desde"))
    acumulado = Decimal("0")
    cursor = desde
    while cursor < hasta:
        tasa = next((fila for fila in tasas if fila.vigente_desde <= cursor and (
            fila.vigente_hasta is None or fila.vigente_hasta >= cursor
        )), None)
        if tasa is None:
            raise ValidationError(
                f"Falta la tasa corriente CMF para el {cursor:%d/%m/%Y}. "
                "Registra el certificado correspondiente antes de calcular."
            )
        fin_tramo = min(hasta, tasa.vigente_hasta + timedelta(days=1)) if tasa.vigente_hasta else hasta
        tasa_anual = tasa.tasa_anual
        if condominio.modalidad_mora == condominio.ModalidadMora.REGLAMENTO_TIPO:
            tasa_anual /= Decimal("2")
        elif condominio.tasa_mora_anual is not None:
            tasa_anual = min(condominio.tasa_mora_anual, tasa_anual / Decimal("2"))
        dias = Decimal((fin_tramo - cursor).days)
        acumulado += capital * tasa_anual / Decimal("100") * dias / Decimal("360")
        cursor = fin_tramo
    return acumulado


def resumen_mora(condominio, hasta_fecha=None):
    """Calcula principal vencido e interés simple devengado por unidad.

    Los pagos reducen primero las obligaciones más antiguas. Los intereses ya
    facturados no se capitalizan ni vuelven a generar interés.
    """
    hasta_fecha = hasta_fecha or timezone.localdate()
    liquidaciones = list(Liquidacion.objects.filter(
        periodo__condominio=condominio,
        periodo__estado=Periodo.Estado.PUBLICADO,
        periodo__publicado__isnull=False,
    ).select_related("periodo", "periodo__condominio").order_by(
        "periodo__publicado", "periodo__anio", "periodo__mes", "id"
    ))
    cargos_por_unidad = {}
    for liquidacion in liquidaciones:
        capital = (
            liquidacion.total_ordinario + liquidacion.fondo_reserva
            + liquidacion.total_extraordinario + liquidacion.consumos
        )
        if capital <= 0:
            continue
        periodo = liquidacion.periodo
        cargos_por_unidad.setdefault(liquidacion.unidad_id, []).append({
            "capital": Decimal(capital),
            "saldo": Decimal(capital),
            "inicio_mora": fecha_inicio_mora(periodo),
            "fecha_emision": periodo.publicado.date(),
            "pagos": [],
            "intereses_facturados": Decimal(liquidacion.intereses or 0),
        })

    pagos = list(PagoRegistrado.objects.filter(
        liquidacion__periodo__condominio=condominio,
        liquidacion__periodo__estado=Periodo.Estado.PUBLICADO,
    ).values_list("liquidacion__unidad_id", "fecha", "monto").order_by("fecha", "id"))
    pagos_por_unidad = {}
    for unidad_id, fecha_pago, monto in pagos:
        pagos_por_unidad.setdefault(unidad_id, []).append((fecha_pago, Decimal(monto)))

    resultados = {}
    for unidad_id, cargos in cargos_por_unidad.items():
        for fecha_pago, monto_pago in pagos_por_unidad.get(unidad_id, []):
            restante = monto_pago
            for cargo in cargos:
                if cargo["fecha_emision"] > fecha_pago or cargo["saldo"] <= 0:
                    continue
                aplicado = min(cargo["saldo"], restante)
                if aplicado:
                    cargo["saldo"] -= aplicado
                    cargo["pagos"].append((fecha_pago, aplicado))
                    restante -= aplicado
                if restante <= 0:
                    break

        principal_vencido = Decimal("0")
        interes_devengado = Decimal("0")
        for cargo in cargos:
            inicio = cargo["inicio_mora"]
            if inicio >= hasta_fecha:
                continue
            saldo_interes = cargo["capital"]
            cursor = inicio
            for fecha_pago, aplicado in cargo["pagos"]:
                if fecha_pago <= inicio:
                    saldo_interes -= aplicado
                    continue
                fecha_corte = min(fecha_pago, hasta_fecha)
                interes_devengado += _interes_en_intervalo(
                    max(saldo_interes, Decimal("0")), cursor, fecha_corte, condominio
                )
                saldo_interes -= aplicado
                cursor = fecha_corte
                if cursor >= hasta_fecha:
                    break
            if cursor < hasta_fecha:
                interes_devengado += _interes_en_intervalo(
                    max(saldo_interes, Decimal("0")), cursor, hasta_fecha, condominio
                )
            principal_vencido += cargo["saldo"]

        ya_facturado = sum((cargo["intereses_facturados"] for cargo in cargos), Decimal("0"))
        resultados[unidad_id] = {
            "principal_vencido": principal_vencido.quantize(Decimal("1"), rounding=ROUND_HALF_UP),
            "interes_devengado": dinero(interes_devengado),
            "interes_por_facturar": dinero(max(interes_devengado - ya_facturado, Decimal("0"))),
        }
    return resultados


@transaction.atomic
def calcular_periodo(periodo: Periodo):
    if periodo.estado != Periodo.Estado.BORRADOR:
        raise ValidationError("Solo se pueden calcular períodos en borrador.")
    unidades = list(Unidad.objects.filter(
        edificio__condominio=periodo.condominio,
        edificio__activo=True,
        activo=True,
    ).select_related("edificio"))
    if not unidades:
        raise ValidationError("El condominio no tiene unidades.")
    if any(u.alicuota is None for u in unidades):
        raise ValidationError("Faltan alícuotas. Carga el porcentaje oficial de todas las unidades antes de calcular.")
    total_alicuotas = sum((u.alicuota for u in unidades), Decimal("0"))
    if total_alicuotas != Decimal("100"):
        raise ValidationError(f"Las alícuotas suman {total_alicuotas}%; deben sumar exactamente 100%.")

    anteriores = saldos_acumulados(periodo.condominio, hasta_periodo=periodo)
    mora_previa = resumen_mora(periodo.condominio, timezone.localdate())
    if not periodo.gastos.exists() and not periodo.lecturas.exists() and not any(saldo > 0 for saldo in anteriores.values()):
        raise ValidationError("Aún no hay gastos ni consumos para este período. Agrega al menos una boleta compartida o una lectura antes de calcular.")

    liquidaciones = {}
    bases_reserva = {}
    for unidad in unidades:
        liq, _ = Liquidacion.objects.update_or_create(periodo=periodo, unidad=unidad, defaults={
            "alicuota_usada": unidad.alicuota, "total_ordinario": 0, "fondo_reserva": 0,
            "total_extraordinario": 0, "consumos": 0, "saldo_anterior": anteriores.get(unidad.id, Decimal("0")),
            "intereses": 0, "pagos_periodo": 0, "total": 0,
        })
        liq.detalles.all().delete()
        liquidaciones[unidad.id] = liq
        bases_reserva[unidad.id] = Decimal("0")

    for gasto in periodo.gastos.select_related("edificio", "unidad"):
        if gasto.alcance == ItemGasto.Alcance.UNIDAD:
            asignaciones = [(gasto.unidad, dinero(gasto.monto))]
        else:
            candidatas = [u for u in unidades if gasto.alcance == ItemGasto.Alcance.CONDOMINIO or u.edificio_id == gasto.edificio_id]
            denominador = sum((u.alicuota for u in candidatas), Decimal("0"))
            if not denominador:
                raise ValidationError(f"No hay unidades con alícuota para distribuir: {gasto.concepto}.")
            asignaciones = [(u, dinero(gasto.monto * u.alicuota / denominador)) for u in candidatas]
            # Ajuste determinista de centavos: la suma distribuida debe coincidir con el gasto fuente.
            diferencia = dinero(gasto.monto - sum((monto for _, monto in asignaciones), Decimal("0")))
            if asignaciones and diferencia:
                unidad_ajuste, monto_ajuste = asignaciones[0]
                asignaciones[0] = (unidad_ajuste, monto_ajuste + diferencia)
        for unidad, monto in asignaciones:
            liq = liquidaciones[unidad.id]
            categoria = "ordinario" if gasto.tipo == ItemGasto.Tipo.ORDINARIO else "extraordinario"
            DetalleCargo.objects.create(liquidacion=liq, concepto=gasto.concepto, categoria=categoria, monto=monto, item_gasto=gasto)
            if categoria == "ordinario": liq.total_ordinario += monto
            else: liq.total_extraordinario += monto
            if categoria == "ordinario" and gasto.alcance != ItemGasto.Alcance.UNIDAD:
                bases_reserva[unidad.id] += monto

    for lectura in periodo.lecturas.select_related("unidad"):
        monto = dinero(lectura.monto)
        liq = liquidaciones[lectura.unidad_id]
        liq.consumos += monto
        DetalleCargo.objects.create(liquidacion=liq,
            concepto=f"Consumo de {lectura.get_tipo_display()} ({lectura.consumo} unidades)", categoria="consumo", monto=monto)

    for liq in liquidaciones.values():
        liq.total_ordinario = dinero(liq.total_ordinario)
        liq.total_extraordinario = dinero(liq.total_extraordinario)
        liq.consumos = dinero(liq.consumos)
        liq.fondo_reserva = dinero(bases_reserva[liq.unidad_id] * periodo.recargo_reserva / Decimal("100"))
        if liq.fondo_reserva:
            DetalleCargo.objects.create(liquidacion=liq, concepto=f"Fondo de reserva ({periodo.recargo_reserva}%)", categoria="reserva", monto=liq.fondo_reserva)
        liq.intereses = mora_previa.get(liq.unidad_id, {}).get("interes_por_facturar", Decimal("0"))
        if liq.intereses:
            DetalleCargo.objects.create(
                liquidacion=liq,
                concepto=f"Interés por mora acumulado al {timezone.localdate():%d/%m/%Y}",
                categoria="intereses",
                monto=liq.intereses,
            )
        liq.total = dinero(liq.total_ordinario + liq.fondo_reserva + liq.total_extraordinario + liq.consumos + liq.saldo_anterior + liq.intereses)
        liq.save()
    return len(liquidaciones)


@transaction.atomic
def publicar_periodo(periodo: Periodo):
    if periodo.estado != Periodo.Estado.BORRADOR:
        raise ValidationError("El período ya fue publicado.")
    if not periodo.liquidaciones.exists():
        raise ValidationError("Primero calcula las liquidaciones.")
    periodo.estado = Periodo.Estado.PUBLICADO
    periodo.publicado = timezone.now()
    periodo.save(update_fields=["estado", "publicado"])

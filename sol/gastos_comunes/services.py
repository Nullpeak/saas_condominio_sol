from decimal import Decimal, ROUND_HALF_UP
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from core.models import Unidad
from .models import Periodo, Liquidacion, DetalleCargo, ItemGasto, LecturaMedidor, PagoRegistrado

CENTAVO = Decimal("0.01")


def dinero(valor):
    return Decimal(valor).quantize(CENTAVO, rounding=ROUND_HALF_UP)


def saldos_acumulados(condominio, hasta_periodo=None):
    """Saldo de cuenta por unidad: cargos mensuales acumulados menos todos los pagos."""
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
    return {f["unidad_id"]: dinero((f["ordinario"] or 0) + (f["reserva"] or 0) +
            (f["extra"] or 0) + (f["consumos"] or 0) + (f["intereses"] or 0) - pagados.get(f["unidad_id"], Decimal("0"))) for f in filas}


@transaction.atomic
def calcular_periodo(periodo: Periodo):
    if periodo.estado != Periodo.Estado.BORRADOR:
        raise ValidationError("Solo se pueden calcular períodos en borrador.")
    unidades = list(Unidad.objects.filter(edificio__condominio=periodo.condominio).select_related("edificio"))
    if not unidades:
        raise ValidationError("El condominio no tiene unidades.")
    if any(u.alicuota is None for u in unidades):
        raise ValidationError("Faltan alícuotas. Carga el porcentaje oficial de todas las unidades antes de calcular.")
    total_alicuotas = sum((u.alicuota for u in unidades), Decimal("0"))
    if total_alicuotas != Decimal("100"):
        raise ValidationError(f"Las alícuotas suman {total_alicuotas}%; deben sumar exactamente 100%.")

    anteriores = saldos_acumulados(periodo.condominio, hasta_periodo=periodo)
    if not periodo.gastos.exists() and not periodo.lecturas.exists() and not any(saldo > 0 for saldo in anteriores.values()):
        raise ValidationError("Aún no hay gastos ni consumos para este período. Agrega al menos una boleta compartida o una lectura antes de calcular.")
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

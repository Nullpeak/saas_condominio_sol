from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Condominio
from gastos_comunes.models import DetalleCargo, ItemGasto, Liquidacion, Periodo
from gastos_comunes.services import calcular_periodo, resumen_mora


@transaction.atomic
def main():
    condo = Condominio.objects.get(pk=2)
    deuda = Periodo.objects.get(condominio=condo, anio=2025, mes=9)
    factura = Periodo.objects.get(condominio=condo, anio=2027, mes=2)
    item = ItemGasto.objects.get(
        periodo=deuda,
        concepto="PRUEBA FICTICIA · deuda vencida hace más de un año · Condominio 2",
    )
    if deuda.estado != Periodo.Estado.PUBLICADO or factura.estado != Periodo.Estado.BORRADOR:
        raise ValidationError("El estado de los períodos de prueba cambió; no se modificaron datos.")

    # $2.1M total deja un saldo vencido comprobable tras los pagos ya presentes
    # en la base local, sin tocar el cargo extraordinario de $12.321.321.312.
    item.monto = Decimal("2100000.00")
    item.save(update_fields=["monto"])
    for liquidacion in Liquidacion.objects.filter(periodo=deuda).select_related("unidad"):
        ordinario = Decimal(item.monto) * liquidacion.unidad.alicuota / Decimal("100")
        reserva = (ordinario * Decimal("5") / Decimal("100")).quantize(Decimal("0.01"))
        DetalleCargo.objects.filter(liquidacion=liquidacion, item_gasto=item).update(monto=ordinario)
        DetalleCargo.objects.filter(liquidacion=liquidacion, categoria="reserva").update(monto=reserva)
        liquidacion.total_ordinario = ordinario
        liquidacion.fondo_reserva = reserva
        liquidacion.total = (
            liquidacion.saldo_anterior + ordinario + reserva
            + liquidacion.total_extraordinario + liquidacion.consumos + liquidacion.intereses
        )
        liquidacion.save(update_fields=["total_ordinario", "fondo_reserva", "total"])

    factura.refresh_from_db()
    calcular_periodo(factura)

    moras = resumen_mora(condo)
    esperado = set(Liquidacion.objects.filter(periodo=deuda).values_list("unidad_id", flat=True))
    if esperado != set(moras) or any(moras[uid]["principal_vencido"] <= 0 for uid in esperado):
        raise ValidationError("La deuda ficticia no quedó vencida en todas las unidades.")
    intereses = dict(Liquidacion.objects.filter(periodo=factura).values_list("unidad__numero_unidad", "intereses"))
    if len(intereses) != 2 or any(monto <= 0 for monto in intereses.values()):
        raise ValidationError("La factura de prueba no incluyó el interés separado para las dos unidades.")

    gigante = ItemGasto.objects.get(pk=11)
    if gigante.monto != Decimal("12321321312.00") or Liquidacion.objects.filter(periodo_id=8, intereses__gt=0).exists():
        raise ValidationError("Se detectó un cambio inesperado en el cargo de 2023.")

    print({
        "deuda_publicada": str(deuda),
        "emision": deuda.publicado.isoformat(),
        "vencimiento": deuda.vencimiento.isoformat(),
        "inicio_mora": "2025-10-16",
        "moras_por_unidad": {uid: {key: str(value) for key, value in data.items()} for uid, data in moras.items()},
        "factura_interes": str(factura),
        "factura_estado": factura.estado,
        "intereses_separados": {unidad: str(monto) for unidad, monto in intereses.items()},
        "cargo_2023_monto": str(gigante.monto),
        "cargo_2023_intereses": "0",
    })


main()

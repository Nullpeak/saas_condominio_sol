from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from core.models import Condominio
from gastos_comunes.models import ItemGasto, Liquidacion, Periodo
from gastos_comunes.services import publicar_periodo, resumen_mora

@transaction.atomic
def main():
    condo = Condominio.objects.get(pk=2)
    deuda = Periodo.objects.get(condominio=condo, anio=2025, mes=9)
    factura = Periodo.objects.get(condominio=condo, anio=2027, mes=2)
    item = ItemGasto.objects.get(pk=12)
    if deuda.estado != Periodo.Estado.PUBLICADO or factura.estado != Periodo.Estado.BORRADOR:
        raise ValidationError("Estados inesperados; se canceló sin cambios.")
    if item.monto != Decimal("2100000.00"):
        raise ValidationError("El cargo ficticio no tiene el monto esperado.")
    moras = resumen_mora(condo)
    expected = set(Liquidacion.objects.filter(periodo=deuda).values_list("unidad_id", flat=True))
    if expected != set(moras) or any(moras[uid]["principal_vencido"] <= 0 for uid in expected):
        raise ValidationError("No todas las unidades tienen saldo vencido.")
    liqs = list(Liquidacion.objects.filter(periodo=factura))
    if len(liqs) != len(expected) or any(liq.intereses <= 0 for liq in liqs):
        raise ValidationError("La factura de interés no está lista para publicar.")
    for liq in liqs:
        liq.saldo_anterior = Decimal("0")
        liq.total_ordinario = Decimal("0")
        liq.fondo_reserva = Decimal("0")
        liq.total_extraordinario = Decimal("0")
        liq.consumos = Decimal("0")
        liq.total = liq.intereses
        liq.save(update_fields=["saldo_anterior", "total_ordinario", "fondo_reserva", "total_extraordinario", "consumos", "total"])
    publicar_periodo(factura)
    gigante = ItemGasto.objects.get(pk=11)
    viejo = Liquidacion.objects.filter(periodo_id=8, intereses__gt=0).exists()
    result = {
        "deuda": str(deuda), "emision": deuda.publicado.isoformat(), "vencimiento": str(deuda.vencimiento),
        "unidad_saldos": {str(uid): {k: str(v) for k,v in moras[uid].items()} for uid in expected},
        "factura_interes_publicada": str(factura), "factura_estado": factura.estado,
        "intereses": {str(liq.unidad_id): str(liq.intereses) for liq in liqs},
        "cargo_id_11": str(gigante.monto), "interes_en_liquidaciones_cargo_2023": viejo,
    }
    print(result)
    if gigante.monto != Decimal("12321321312.00") or viejo:
        raise ValidationError("El cargo grande cambió o recibió interés.")
main()

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.models import Condominio, Unidad
from gastos_comunes.models import ItemGasto, Periodo, TasaInteresCorriente
from gastos_comunes.services import calcular_periodo, publicar_periodo, resumen_mora


CMF_URL = "https://www.cmfchile.cl/portal/estadisticas/626/articles-{article}_recurso_1.pdf"
RATES = [
    (date(2025, 10, 15), Decimal("30.99"), "10/2025", "99545"),
    (date(2025, 11, 15), Decimal("30.72"), "11/2025", "100577"),
    (date(2025, 12, 15), Decimal("30.72"), "12/2025", "101939"),
    (date(2026, 1, 15), Decimal("30.91"), "01/2026", "102814"),
    (date(2026, 2, 14), Decimal("29.00"), "02/2026", "103637"),
    (date(2026, 3, 14), Decimal("29.85"), "03/2026", "108766"),
    (date(2026, 4, 15), Decimal("29.68"), "04/2026", "109441"),
    (date(2026, 5, 15), Decimal("30.90"), "05/2026", "110433"),
    (date(2026, 6, 15), Decimal("31.54"), "06/2026", "111130"),
    (date(2026, 7, 15), Decimal("30.89"), "07/2026", "111825"),
    (date(2026, 8, 14), Decimal("31.65"), "08/2026", "112621"),
    (date(2026, 9, 15), Decimal("31.29"), "09/2026", "113468"),
    (date(2026, 10, 15), Decimal("31.33"), "10/2026", "114222"),
]


@transaction.atomic
def main():
    condo = Condominio.objects.get(pk=2)
    unidades = list(Unidad.objects.filter(
        edificio__condominio=condo, edificio__activo=True, activo=True,
    ).order_by("pk"))
    if len(unidades) != 2 or sum((u.alicuota for u in unidades), Decimal("0")) != Decimal("100"):
        raise ValidationError("Cambió el universo esperado del Condominio 2; no se insertaron datos.")

    if Periodo.objects.filter(condominio=condo, anio=2025, mes=9).exists():
        raise ValidationError("Ya existe el período 09/2025; no se duplicó la deuda de prueba.")
    if Periodo.objects.filter(condominio=condo, anio=2027, mes=2).exists():
        raise ValidationError("Ya existe el período 02/2027; no se duplicó la factura de prueba.")

    for index, (effective, rate, cert, article) in enumerate(RATES):
        until = RATES[index + 1][0] - timedelta(days=1) if index + 1 < len(RATES) else None
        values = {
            "vigente_hasta": until,
            "segmento": TasaInteresCorriente.SEGMENTO_LEGAL,
            "tasa_anual": rate,
            "certificado": f"CMF {cert} · tasa consolidada 90+ días, hasta 200 UF",
            "fuente_url": CMF_URL.format(article=article),
        }
        existing = TasaInteresCorriente.objects.filter(vigente_desde=effective).first()
        if existing:
            if (existing.tasa_anual, existing.certificado) != (rate, values["certificado"]):
                raise ValidationError(f"La tasa CMF de {effective} ya existe con otros datos.")
            for key, value in values.items():
                setattr(existing, key, value)
            existing.full_clean()
            existing.save()
        else:
            TasaInteresCorriente.objects.create(vigente_desde=effective, **values)

    creador_id = Periodo.objects.filter(condominio=condo).order_by("pk").values_list("creado_por_id", flat=True).first()
    if not creador_id:
        raise ValidationError("No hay un creador válido para los períodos de prueba.")

    fecha_emision = timezone.make_aware(datetime(2025, 10, 5, 12, 0))
    deuda = Periodo.objects.create(
        condominio=condo, anio=2025, mes=9, vencimiento=date(2025, 10, 15),
        recargo_reserva=Decimal("5.00"), creado_por_id=creador_id,
    )
    Periodo.objects.filter(pk=deuda.pk).update(creado=fecha_emision)
    ItemGasto.objects.create(
        periodo=deuda,
        concepto="PRUEBA FICTICIA · deuda vencida hace más de un año · Condominio 2",
        tipo=ItemGasto.Tipo.ORDINARIO,
        alcance=ItemGasto.Alcance.CONDOMINIO,
        monto=Decimal("2000.00"),
    )
    deuda.refresh_from_db()
    calcular_periodo(deuda)
    publicar_periodo(deuda)
    Periodo.objects.filter(pk=deuda.pk).update(creado=fecha_emision, publicado=fecha_emision)
    deuda.refresh_from_db()

    factura = Periodo.objects.create(
        condominio=condo, anio=2027, mes=2, vencimiento=date(2027, 3, 10),
        recargo_reserva=Decimal("5.00"), creado_por_id=creador_id,
    )
    calcular_periodo(factura)
    factura.refresh_from_db()

    moras = resumen_mora(condo)
    print({
        "deuda_periodo": str(deuda),
        "fecha_emision": deuda.publicado.isoformat(),
        "vencimiento": deuda.vencimiento.isoformat(),
        "inicio_mora": "2025-10-16",
        "factura_interes_periodo": str(factura),
        "factura_estado": factura.estado,
        "unidades": [u.numero_unidad for u in unidades],
        "moras": {uid: {key: str(value) for key, value in data.items()} for uid, data in moras.items()},
        "interes_facturado_por_unidad": list(factura.liquidaciones.order_by("unidad_id").values_list("unidad__numero_unidad", "intereses")),
    })


main()

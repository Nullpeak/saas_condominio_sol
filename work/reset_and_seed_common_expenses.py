from datetime import date, datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from core.models import Condominio, Unidad
from gastos_comunes.models import ItemGasto, Liquidacion, PagoOnline, Periodo, TasaInteresCorriente
from gastos_comunes.services import calcular_periodo, fecha_inicio_mora, publicar_periodo, resumen_mora, saldos_acumulados

TZ = ZoneInfo("America/Santiago")

@transaction.atomic
def main():
    if Periodo.objects.count() != 9:
        raise ValidationError("Cambió la cantidad de períodos desde la revisión; se canceló.")
    condo = Condominio.objects.get(pk=2)
    unidades = list(Unidad.objects.filter(edificio__condominio=condo, edificio__activo=True, activo=True).select_related("dueno", "arrendatario").order_by("id"))
    residentes = set()
    for unidad in unidades:
        if unidad.dueno_id and unidad.dueno.activo: residentes.add(unidad.dueno_id)
        if unidad.arrendatario_id and unidad.arrendatario.activo: residentes.add(unidad.arrendatario_id)
    if not unidades or not residentes or any(u.alicuota is None for u in unidades):
        raise ValidationError("Condominio 2 no tiene unidades/residentes o alícuotas suficientes.")
    if sum((u.alicuota for u in unidades), Decimal("0")) != Decimal("100"):
        raise ValidationError("Las alícuotas de Condominio 2 no suman 100%.")
    if not TasaInteresCorriente.objects.exists():
        raise ValidationError("No hay tasas CMF; no se puede calcular la mora.")

    online_count, _ = PagoOnline.objects.all().delete()
    deleted_count, deleted_by_model = Periodo.objects.all().delete()
    if Periodo.objects.exists() or Liquidacion.objects.exists() or ItemGasto.objects.exists():
        raise ValidationError("No se limpió completamente el historial de gastos comunes.")

    creador_id = min(residentes)
    viejo = Periodo.objects.create(condominio=condo, anio=2025, mes=10, vencimiento=date(2025, 10, 15), recargo_reserva=Decimal("5"), creado_por_id=creador_id)
    ItemGasto.objects.create(periodo=viejo, concepto="PRUEBA FICTICIA · gasto común vencido · Condominio 2", tipo=ItemGasto.Tipo.ORDINARIO, alcance=ItemGasto.Alcance.CONDOMINIO, monto=Decimal("100000.00"))
    calcular_periodo(viejo)
    publicar_periodo(viejo)
    viejo.publicado = timezone.make_aware(datetime.combine(date(2025, 10, 1), time(12, 0)), TZ)
    viejo.save(update_fields=["publicado"])

    al_dia = Periodo.objects.create(condominio=condo, anio=2026, mes=11, vencimiento=date(2026, 11, 20), recargo_reserva=Decimal("5"), creado_por_id=creador_id)
    ItemGasto.objects.create(periodo=al_dia, concepto="PRUEBA FICTICIA · gasto común vigente para pagar · Condominio 2", tipo=ItemGasto.Tipo.ORDINARIO, alcance=ItemGasto.Alcance.CONDOMINIO, monto=Decimal("80000.00"))
    calcular_periodo(al_dia)
    publicar_periodo(al_dia)
    al_dia.publicado = timezone.make_aware(datetime.combine(date(2026, 11, 10), time(12, 0)), TZ)
    al_dia.save(update_fields=["publicado"])

    mora = resumen_mora(condo, hasta_fecha=date(2026, 11, 10))
    expected = {u.id for u in unidades}
    if set(mora) != expected or any(mora[uid]["principal_vencido"] <= 0 or mora[uid]["interes_devengado"] <= 0 for uid in expected):
        raise ValidationError("Falta principal vencido o interés de mora en alguna unidad.")
    if any(liq.intereses <= 0 for liq in Liquidacion.objects.filter(periodo=al_dia)):
        raise ValidationError("El período vigente no tiene la línea separada de interés de mora.")
    saldos = saldos_acumulados(condo, al_dia)
    print({"periodos_eliminados":deleted_by_model.get("gastos_comunes.Periodo",0), "registros_en_cascada":deleted_by_model, "ordenes_pago_online_eliminadas":online_count, "periodos_restantes":list(Periodo.objects.values_list("condominio_id","anio","mes","estado")), "unidades":[u.numero_unidad for u in unidades], "residentes_ids":sorted(residentes), "gasto_vencido":{"periodo":"10/2025","emision":viejo.publicado.isoformat(),"vencimiento":str(viejo.vencimiento),"inicio_mora":str(fecha_inicio_mora(viejo)),"monto":"100000.00"}, "gasto_al_dia":{"periodo":"11/2026","emision":al_dia.publicado.isoformat(),"vencimiento":str(al_dia.vencimiento),"monto":"80000.00"}, "mora_por_unidad":{str(uid):{k:str(v) for k,v in valores.items()} for uid,valores in mora.items()}, "intereses_facturados":{liq.unidad.numero_unidad:str(liq.intereses) for liq in Liquidacion.objects.filter(periodo=al_dia).select_related("unidad")}, "saldo_acumulado":{str(uid):str(saldo) for uid,saldo in saldos.items()}, "tasas_cmf_conservadas":TasaInteresCorriente.objects.count()})
main()

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.utils import timezone
from core.models import Condominio, Edificio, Unidad, Usuario
import uuid


class Periodo(models.Model):
    class Estado(models.TextChoices):
        BORRADOR = "borrador", "Borrador"
        PUBLICADO = "publicado", "Publicado"

    condominio = models.ForeignKey(Condominio, on_delete=models.CASCADE, related_name="periodos_gastos")
    anio = models.PositiveSmallIntegerField(validators=[MinValueValidator(2000), MaxValueValidator(2100)])
    mes = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(12)])
    vencimiento = models.DateField()
    recargo_reserva = models.DecimalField(max_digits=5, decimal_places=2, default=5,
        validators=[MinValueValidator(5), MaxValueValidator(100)])
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.BORRADOR)
    creado_por = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="periodos_creados")
    creado = models.DateTimeField(auto_now_add=True)
    publicado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-anio", "-mes"]
        constraints = [models.UniqueConstraint(fields=["condominio", "anio", "mes"], name="periodo_gasto_unico_condominio_mes")]

    def __str__(self):
        return f"{self.mes:02d}/{self.anio} · {self.condominio}"


class ItemGasto(models.Model):
    class Tipo(models.TextChoices):
        ORDINARIO = "ordinario", "Ordinario"
        EXTRAORDINARIO = "extraordinario", "Extraordinario"

    class Alcance(models.TextChoices):
        CONDOMINIO = "condominio", "Todo el condominio"
        EDIFICIO = "edificio", "Edificio"
        UNIDAD = "unidad", "Unidad específica"

    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="gastos")
    concepto = models.CharField(max_length=140)
    tipo = models.CharField(max_length=18, choices=Tipo.choices)
    alcance = models.CharField(max_length=12, choices=Alcance.choices, default=Alcance.CONDOMINIO)
    edificio = models.ForeignKey(Edificio, on_delete=models.PROTECT, null=True, blank=True)
    unidad = models.ForeignKey(Unidad, on_delete=models.PROTECT, null=True, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0.01)])
    respaldo = models.FileField(upload_to="gastos_comunes/respaldo/%Y/%m/", blank=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.periodo_id and self.edificio_id and self.edificio.condominio_id != self.periodo.condominio_id:
            raise ValidationError("El edificio no pertenece al condominio del período.")
        if self.periodo_id and self.unidad_id and self.unidad.edificio.condominio_id != self.periodo.condominio_id:
            raise ValidationError("La unidad no pertenece al condominio del período.")
        if self.alcance == self.Alcance.EDIFICIO and not self.edificio_id:
            raise ValidationError("Selecciona el edificio para este gasto.")
        if self.alcance == self.Alcance.UNIDAD and not self.unidad_id:
            raise ValidationError("Selecciona la unidad para este gasto.")
        if self.alcance == self.Alcance.CONDOMINIO and (self.edificio_id or self.unidad_id):
            raise ValidationError("Un gasto para todo el condominio no debe llevar un edificio ni una unidad seleccionados.")
        if self.alcance == self.Alcance.EDIFICIO and self.unidad_id:
            raise ValidationError("Un gasto de edificio no debe llevar una unidad seleccionada.")
        if self.alcance == self.Alcance.UNIDAD and self.edificio_id:
            raise ValidationError("Un gasto de unidad no debe llevar un edificio seleccionado.")


class LecturaMedidor(models.Model):
    class Tipo(models.TextChoices):
        AGUA = "agua", "Agua"
        GAS = "gas", "Gas"
        OTRO = "otro", "Otro"

    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="lecturas")
    unidad = models.ForeignKey(Unidad, on_delete=models.CASCADE, related_name="lecturas_medidor")
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    lectura_anterior = models.DecimalField(max_digits=14, decimal_places=3)
    lectura_actual = models.DecimalField(max_digits=14, decimal_places=3)
    tarifa = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(0)])

    class Meta:
        constraints = [models.UniqueConstraint(fields=["periodo", "unidad", "tipo"], name="lectura_unica_por_periodo_unidad_tipo")]

    @property
    def consumo(self):
        return self.lectura_actual - self.lectura_anterior

    @property
    def monto(self):
        return self.consumo * self.tarifa

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.lectura_actual < self.lectura_anterior:
            raise ValidationError("La lectura actual no puede ser menor que la anterior.")
        if self.periodo_id and self.unidad_id and self.unidad.edificio.condominio_id != self.periodo.condominio_id:
            raise ValidationError("La unidad no pertenece al condominio del período.")


class Liquidacion(models.Model):
    periodo = models.ForeignKey(Periodo, on_delete=models.CASCADE, related_name="liquidaciones")
    unidad = models.ForeignKey(Unidad, on_delete=models.PROTECT, related_name="liquidaciones")
    alicuota_usada = models.DecimalField(max_digits=9, decimal_places=6)
    total_ordinario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fondo_reserva = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_extraordinario = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    consumos = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    saldo_anterior = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    intereses = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    pagos_periodo = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    calculada = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["periodo", "unidad"], name="liquidacion_unica_periodo_unidad")]

    @property
    def saldo(self):
        return self.total - self.pagado

    @property
    def pagado(self):
        return self.pagos.aggregate(total=models.Sum("monto"))["total"] or 0


class PagoOnline(models.Model):
    class Estado(models.TextChoices):
        CREADO = "creado", "Creado"
        PENDIENTE = "pendiente", "Pendiente"
        APROBADO = "aprobado", "Aprobado"
        RECHAZADO = "rechazado", "Rechazado"
        CANCELADO = "cancelado", "Cancelado"
        ERROR = "error", "Error de creación"

    liquidacion = models.ForeignKey(Liquidacion, on_delete=models.PROTECT, related_name="pagos_online")
    usuario = models.ForeignKey(Usuario, on_delete=models.PROTECT, related_name="pagos_online")
    referencia_externa = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    clave_idempotencia = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    orden_proveedor = models.CharField(max_length=80, unique=True, null=True, blank=True)
    url_pago = models.URLField(max_length=1000, blank=True)
    monto = models.DecimalField(max_digits=14, decimal_places=0, validators=[MinValueValidator(1)])
    estado = models.CharField(max_length=12, choices=Estado.choices, default=Estado.CREADO)
    detalle_estado = models.CharField(max_length=100, blank=True)
    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)
    pagado = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-creado", "-id"]

    def __str__(self):
        return f"Pago {self.referencia_externa} · {self.get_estado_display()}"


class DetalleCargo(models.Model):
    liquidacion = models.ForeignKey(Liquidacion, on_delete=models.CASCADE, related_name="detalles")
    concepto = models.CharField(max_length=180)
    categoria = models.CharField(max_length=30)
    monto = models.DecimalField(max_digits=14, decimal_places=2)
    item_gasto = models.ForeignKey(ItemGasto, on_delete=models.SET_NULL, null=True, blank=True)


class PagoRegistrado(models.Model):
    liquidacion = models.ForeignKey(Liquidacion, on_delete=models.CASCADE, related_name="pagos")
    monto = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(0.01)])
    fecha = models.DateField(default=timezone.localdate)
    metodo = models.CharField(max_length=30, blank=True)
    referencia = models.CharField(max_length=100, blank=True)
    registrado_por = models.ForeignKey(Usuario, on_delete=models.PROTECT)
    pago_online = models.OneToOneField(PagoOnline, on_delete=models.SET_NULL, null=True, blank=True, related_name="pago_registrado")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha", "-id"]

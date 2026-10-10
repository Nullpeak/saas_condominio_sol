import os
from io import BytesIO
from datetime import date
from django.conf import settings
from django.utils.text import slugify
from PIL import Image, ImageOps
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db.models import F, Q
from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import models, transaction
from .validators import formatear_rut, validar_rut

# Create your models here.
class Condominio(models.Model):
    nombre = models.CharField(max_length=150)
    direccion = models.CharField(max_length=255)
    ciudad = models.ForeignKey("Ciudad",on_delete=models.PROTECT,related_name="condominios",)
    portada = models.ImageField(upload_to="condominios/portadas/", blank=True)
    edificios = models.PositiveSmallIntegerField(default=1,validators=[MinValueValidator(1)],)
    creado = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "condominio"

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        es_nuevo = self._state.adding
        with transaction.atomic():
            super().save(*args, **kwargs)
            if es_nuevo:
                Edificio.objects.bulk_create(
                    [
                        Edificio(condominio=self, numero=i, nombre=f"Edificio {i}")
                        for i in range(1, self.edificios + 1)
                    ]
                )
#
class Edificio(models.Model):
    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="edificios_set",)
    numero = models.PositiveSmallIntegerField()  # la "X" del numero de unidad
    nombre = models.CharField(max_length=50)
    numero_pisos = models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(2), MaxValueValidator(20)],)
    viviendas_por_piso = models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(1)],)
    subterraneo = models.BooleanField(default=False)

    class Meta:
        db_table = "edificio"
        constraints = [
            models.UniqueConstraint(fields=["condominio", "numero"],name="edificio_numero_unico_por_condominio",),
            models.UniqueConstraint(fields=["condominio", "nombre"],name="edificio_nombre_unico_por_condominio",),
        ]

    def __str__(self):
        return f"{self.nombre} - {self.condominio}"

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            if (
                self.numero_pisos
                and self.viviendas_por_piso
                and not self.unidades.exists()
            ):
                self.generar_unidades()

    def generar_unidades(self):
        ancho = len(str(self.viviendas_por_piso))
        Unidad.objects.bulk_create(
            [
                Unidad(
                    edificio=self,
                    piso=piso,
                    posicion=pos,
                    numero_unidad=f"{self.numero}-{piso}{pos:0{ancho}d}",
                )
                for piso in range(1, self.numero_pisos + 1)
                for pos in range(1, self.viviendas_por_piso + 1)
            ]
        )
#
class Unidad(models.Model):
    edificio = models.ForeignKey(Edificio,on_delete=models.CASCADE,related_name="unidades",)
    piso = models.PositiveSmallIntegerField()
    posicion = models.PositiveSmallIntegerField()
    numero_unidad = models.CharField(max_length=10)
    dueno = models.ForeignKey("Usuario",on_delete=models.PROTECT,null=True,blank=True,related_name="unidades_propias",)
    arrendatario = models.ForeignKey("Usuario",on_delete=models.SET_NULL,null=True,blank=True,related_name="unidades_arrendadas",)

    def __str__(self):
        return f"Unidad {self.numero_unidad} - {self.edificio}"

    @property
    def estacionamientos_extra(self):
        # El primer estacionamiento va incluido, desde el segundo se cobra
        return max(self.estacionamientos.count() - 1, 0)

    class Meta:
        db_table = "unidad"
        constraints = [
            models.UniqueConstraint(
                fields=["edificio", "numero_unidad"],
                name="unidad_numero_unico_por_edificio",
            ),
            models.UniqueConstraint(
                fields=["edificio", "piso", "posicion"],
                name="unidad_posicion_unica_por_edificio",
            ),
        ]

    def __str__(self):
        return f"Unidad {self.numero_unidad} - {self.edificio}"

def user_directory_path(instance, filename):
    # user es opcional, así que hay que tener un respaldo si no existe
    carpeta = instance.user.username if instance.user_id else (instance.slug or "sin_usuario")
    return f"user_images/{carpeta}/{filename}"

class Ciudad(models.Model):
    nombre = models.CharField(max_length=50, unique=True)

    class Meta:
        db_table = "ciudad"
        verbose_name_plural = "ciudades"

    def __str__(self):
        return self.nombre
#
class Usuario(models.Model):
    class Rol(models.TextChoices):
        SUPER = "Super Administrador", "super administrador"
        ADMINISTRADOR = "Administrador", "administrador"
        GESTOR = "Gestor", "gestor"
        RESIDENTE = "Residente", "residente"
    user = models.OneToOneField(settings.AUTH_USER_MODEL,on_delete=models.CASCADE,null=True,blank=True,)
    condominio = models.ForeignKey("Condominio",on_delete=models.SET_NULL,null=True,blank=True,related_name="staff",)
    nombre = models.CharField(max_length=30)
    nombre2 = models.CharField(max_length=30, blank=True)
    apellido = models.CharField(max_length=30)
    apellido2 = models.CharField(max_length=30, blank=True)
    avatar = models.ImageField(upload_to=user_directory_path, null=True, blank=True)
    rut = models.CharField(max_length=12,blank=True,null=True,unique=True,validators=[validar_rut],)
    rol = models.CharField(verbose_name="Rol",choices=Rol.choices,default=Rol.RESIDENTE)
    slug = models.SlugField(unique=True, blank=True, null=True)
    email = models.EmailField(max_length=50, unique=True)
    telefono = models.CharField(max_length=15, blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "usuario"

    def __str__(self):
        return f"{self.nombre} {self.apellido}"

    def _generar_slug(self):
        base_slug = slugify(f"{self.nombre} {self.apellido}")
        slug = base_slug
        num = 1
        while Usuario.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base_slug}-{num}"
            num += 1
        return slug

    def _comprimir_avatar(self):
        img = ImageOps.exif_transpose(Image.open(self.avatar)).convert("RGB")
        img.thumbnail((512, 512))
        salida = BytesIO()
        img.save(salida, format="JPEG", quality=70)
        nombre = os.path.splitext(os.path.basename(self.avatar.name))[0] + ".jpg"
        self.avatar.save(nombre, ContentFile(salida.getvalue()), save=False)

    def _normalizar_rut(self):
        # Un RUT vacío se guarda como None: si fuera "", el unique fallaría con el segundo usuario sin RUT
        self.rut = formatear_rut(self.rut) if self.rut else None

    def clean(self):
        super().clean()
        self._normalizar_rut()

    def save(self, *args, **kwargs):
        self._normalizar_rut()
        if not self.slug:
            self.slug = self._generar_slug()
        if self.avatar and not self.avatar._committed:
            self._comprimir_avatar()
        super().save(*args, **kwargs)
#
class Costo(models.Model):
    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="costos",)
    nombre = models.CharField(max_length=100)
    monto = models.PositiveIntegerField()
    frecuente = models.BooleanField(default=False)

    class Meta:
        db_table = "costo"

    def __str__(self):
        return f"{self.nombre} - ${self.monto}"
#
class AreaComun(models.Model):
    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="areas_comunes",)
    nombre = models.CharField(max_length=100)
    descripcion = models.TextField(blank=True)
    capacidad = models.PositiveSmallIntegerField(null=True, blank=True)
    reservable = models.BooleanField(default=False)
    duracion_maxima = models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(1)],)
    requiere_reserva = models.BooleanField(default=True,)
    permite_reservas_consecutivas = models.BooleanField(default=False,)
    dias_anticipacion_maxima = models.PositiveSmallIntegerField(null=True,blank=True,validators=[MinValueValidator(1)],)
    costo = models.ForeignKey(Costo,on_delete=models.SET_NULL,null=True,blank=True,related_name="areas_comunes",)

    class Meta:
        db_table = "area_comun"
        verbose_name_plural = "areas comunes"

    def __str__(self):
        return f"{self.nombre} - {self.condominio}"
#
class HorarioAreaComun(models.Model):
    area_comun = models.ForeignKey(
        AreaComun,
        on_delete=models.CASCADE,
        related_name="horarios",
    )

    # 0 = lunes
    # 1 = martes
    # 2 = miércoles
    # 3 = jueves
    # 4 = viernes
    # 5 = sábado
    # 6 = domingo
    dia_semana = models.PositiveSmallIntegerField()

    hora_inicio = models.TimeField()
    hora_fin = models.TimeField()

    class Meta:
        db_table = "horario_area_comun"
        ordering = [
            "dia_semana",
            "hora_inicio",
        ]

    def __str__(self):
        return (
            f"{self.area_comun.nombre} - "
            f"{self.hora_inicio.strftime('%H:%M')} a "
            f"{self.hora_fin.strftime('%H:%M')}"
        )

    def clean(self):
        super().clean()

        if self.dia_semana < 0 or self.dia_semana > 6:
            raise ValidationError(
                "El día de la semana debe estar entre 0 y 6."
            )

        if self.hora_inicio >= self.hora_fin:
            raise ValidationError(
                "La hora de inicio debe ser anterior "
                "a la hora de término."
            )

#
class Pago(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PAGADO = "pagado", "Pagado"
        ATRASADO = "atrasado", "Atrasado"

    class MetodoPago(models.TextChoices):
        EFECTIVO = "efectivo", "Efectivo"
        DEBITO = "debito", "Tarjeta de débito"
        CREDITO = "credito", "Tarjeta de crédito"
        TRANSFERENCIA = "transferencia", "Transferencia"

    unidad = models.ForeignKey(
        Unidad,
        on_delete=models.CASCADE,
        related_name="pagos",
    )
    costo = models.ForeignKey(
        Costo,
        on_delete=models.PROTECT,
        related_name="pagos",
    )
    cantidad = models.PositiveSmallIntegerField(default=1)
    monto = models.PositiveIntegerField()
    detalles = models.CharField(max_length=255, blank=True)
    fecha_pago = models.DateField()
    fecha_pagado = models.DateField(null=True, blank=True)
    estado = models.CharField(
        max_length=10,
        choices=Estado.choices,
        default=Estado.PENDIENTE,
    )
    cantidad_residentes = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(20)],)
    metodo_pago = models.CharField(
        max_length=15,
        choices=MetodoPago.choices,
        blank=True,
    )

    class Meta:
        db_table = "pago"
        indexes = [
            models.Index(fields=["estado"]),
            models.Index(fields=["fecha_pago"]),
        ]
    @property
    def esta_atrasado(self):
        return self.estado == self.Estado.PENDIENTE and self.fecha_pago < date.today()
    
    def __str__(self):
        return f"{self.costo.nombre} - {self.unidad} ({self.estado})"
#
class Reservacion(models.Model):
    class Tipo(models.TextChoices):
        INVITADO = "invitado", "Invitado"
        AREA_COMUN = "area_comun", "Área común"

    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        APROBADO = "aprobado", "Aprobado"
        RECHAZADO = "rechazado", "Rechazado"
        CANCELADO = "cancelado", "Cancelado"

    tipo_reserva = models.CharField(max_length=10, choices=Tipo.choices)
    usuario = models.ForeignKey(Usuario,on_delete=models.PROTECT,related_name="reservaciones",)
    unidad = models.ForeignKey(Unidad,on_delete=models.PROTECT,related_name="reservaciones",)
    fecha = models.DateTimeField(auto_now_add=True)
    fecha_reserva = models.DateTimeField()
    fin_reserva = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)
    notas = models.TextField(blank=True)
    area_comun = models.ForeignKey(AreaComun,on_delete=models.PROTECT,null=True,blank=True,related_name="reservaciones",)
    nombre_invitado = models.CharField(max_length=100, blank=True)
    invitado = models.ForeignKey("Invitado",on_delete=models.SET_NULL,null=True,blank=True,related_name="reservaciones",)

    class Meta:
        db_table = "reservacion"
        indexes = [
            models.Index(fields=["estado"]),
            models.Index(fields=["fecha_reserva"]),
        ]

    def __str__(self):
        return f"Reserva #{self.pk} - {self.get_tipo_reserva_display()}"

    def clean(self):
        super().clean()
        if not self.fin_reserva:
            raise ValidationError("Debes indicar cuándo termina la reserva.")
        if self.tipo_reserva == self.Tipo.AREA_COMUN:
            if not self.area_comun_id:
                raise ValidationError("Debes seleccionar un área común.")
            if self.nombre_invitado:
                raise ValidationError("Una reserva de área común no lleva invitado.")
        elif self.tipo_reserva == self.Tipo.INVITADO:
            if not self.nombre_invitado:
                raise ValidationError("Debes indicar el nombre del invitado.")
            if self.area_comun_id:
                raise ValidationError("Una reserva de invitado no lleva área común.")

    def save(self, *args, **kwargs):
        crear_invitado = (
            self.tipo_reserva == self.Tipo.INVITADO
            and self.nombre_invitado
            and not self.invitado_id
        )
        with transaction.atomic():
            super().save(*args, **kwargs)
            if crear_invitado:
                self.invitado = Invitado.objects.create(
                    condominio=self.unidad.edificio.condominio,
                    edificio=self.unidad.edificio,
                    unidad=self.unidad,
                    nombre=self.nombre_invitado,
                )
                super().save(update_fields=["invitado"])
#
class Invitado(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        CHECK_IN = "check_in", "Check-in"
        CHECK_OUT = "check_out", "Check-out"

    condominio = models.ForeignKey(Condominio,on_delete=models.SET_NULL,null=True,blank=True,related_name="invitados",)
    edificio = models.ForeignKey(Edificio,on_delete=models.SET_NULL,null=True,blank=True,related_name="invitados",)
    unidad = models.ForeignKey(Unidad,on_delete=models.SET_NULL,null=True,blank=True,related_name="invitados",)
    nombre = models.CharField(max_length=100)
    patente_vehiculo = models.CharField(max_length=10, blank=True)
    tiempo_llegada = models.DateTimeField(null=True, blank=True)
    tiempo_salida = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=Estado.choices, default=Estado.PENDIENTE)

    class Meta:
        db_table = "invitado"

    def __str__(self):
        return f"{self.nombre} - {self.unidad}"
#
class Ticket(models.Model):
    class Estado(models.TextChoices):
        ABIERTO = "abierto", "Abierto"
        EN_REVISION = "en_revision", "En revisión"
        RESUELTO = "resuelto", "Resuelto"
        CERRADO = "cerrado", "Cerrado"

    class Prioridad(models.TextChoices):
        BAJA = "baja", "Baja"
        MEDIA = "media", "Media"
        ALTA = "alta", "Alta"

    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="tickets",)
    usuario = models.ForeignKey(Usuario,on_delete=models.PROTECT,related_name="tickets_creados",)
    titulo = models.CharField(max_length=150)
    descripcion = models.TextField()
    estado = models.CharField(max_length=15, choices=Estado.choices, default=Estado.ABIERTO)
    prioridad = models.CharField(max_length=5, choices=Prioridad.choices, blank=True)
    asignado = models.ForeignKey(Usuario,on_delete=models.SET_NULL,null=True,blank=True,related_name="tickets_asignados",)
    creado = models.DateTimeField(auto_now_add=True)
    resuelto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ticket"
        indexes = [
            models.Index(fields=["estado"]),
        ]

    def __str__(self):
        return f"#{self.pk} - {self.titulo}"

    def clean(self):
        super().clean()
        if self.asignado_id and self.asignado.rol.nombre not in ("Gestor", "Administrador"):
            raise ValidationError(
                "Solo se puede asignar a un usuario con rol de gestor o administrador."
            )
        if self.asignado_id and self.asignado.condominio_id != self.condominio_id:
            raise ValidationError(
                "El usuario asignado no pertenece a este condominio."
            )


def ticket_archivo_path(instance, filename):
    return f"tickets/{instance.ticket_id}/{filename}"
#
class TicketArchivo(models.Model):
    ticket = models.ForeignKey(Ticket,on_delete=models.CASCADE,related_name="pruebas",)
    archivo = models.FileField(upload_to=ticket_archivo_path)
    subido = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ticket_archivo"

    def __str__(self):
        return f"Prueba de ticket #{self.ticket_id}"
#
class Anuncio(models.Model):
    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="anuncios",)
    usuario = models.ForeignKey(Usuario,on_delete=models.PROTECT,related_name="anuncios",)
    titulo = models.CharField(max_length=150)
    contenido = models.TextField()
    creado = models.DateTimeField(auto_now_add=True)
    pin = models.BooleanField(default=False)

    class Meta:
        db_table = "anuncio"
        ordering = ["-pin", "-creado"]

    def __str__(self):
        return self.titulo

    def clean(self):
        super().clean()
        if self.usuario.rol.nombre not in ("Gestor", "Administrador"):
            raise ValidationError(
                "Solo un gestor o administrador puede crear anuncios."
            )
        if self.usuario.condominio_id != self.condominio_id:
            raise ValidationError(
                "El usuario no pertenece a este condominio."
            )
#
class EspacioEstacionamiento(models.Model):
    class Tipo(models.TextChoices):
        RESIDENTE = "residente", "Residente"
        VISITA = "visita", "Visita"

    condominio = models.ForeignKey(Condominio,on_delete=models.CASCADE,related_name="estacionamientos",)
    edificio = models.ForeignKey(Edificio,on_delete=models.SET_NULL,null=True,blank=True,related_name="estacionamientos",)
    numero = models.CharField(max_length=10)
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    subterraneo = models.BooleanField(default=False)
    unidad = models.ForeignKey(Unidad,on_delete=models.SET_NULL, null=True,blank=True,related_name="estacionamientos",)
    ocupado = models.BooleanField(default=False)

    class Meta:
        db_table = "espacio_estacionamiento"
        constraints = [
            models.UniqueConstraint(
                fields=["condominio", "numero"],
                name="estacionamiento_numero_unico_por_condominio",
            )
        ]

    def __str__(self):
        return f"{self.numero} ({self.get_tipo_display()})"

    def clean(self):
        super().clean()
        if self.tipo == self.Tipo.VISITA and self.unidad_id:
            raise ValidationError(
                "Un estacionamiento de visita no puede tener unidad asignada."
            )
        if self.tipo == self.Tipo.RESIDENTE and not self.unidad_id:
            raise ValidationError(
                "Un estacionamiento de residente debe tener una unidad asignada."
            )
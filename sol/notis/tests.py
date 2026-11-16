from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from anuncios.models import AnuncioResidente
from core.models import AreaComun, Ciudad, Condominio, Reservacion, Usuario
from gastos_comunes.models import Liquidacion, PagoOnline, Periodo
from gastos_comunes.services import publicar_periodo
from invitaciones.models import Invitacion
from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento

from . import tasks
from .models import Notification

Rol = Usuario.Rol


class BaseNotis(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Santiago")
        cls.condo = Condominio.objects.create(nombre="Los Robles", direccion="x", ciudad=ciudad)
        ed = cls.condo.edificios_set.first()
        ed.numero_pisos, ed.viviendas_por_piso = 1, 2
        ed.save()
        cls.unidad, cls.unidad2 = ed.unidades.all()[:2]

        def perfil(username, rol):
            u = get_user_model().objects.create_user(username, f"{username}@x.cl", "clave-segura-123")
            return Usuario.objects.create(user=u, nombre=username, apellido="T", email=f"{username}@x.cl",
                                          rol=rol, condominio=cls.condo)

        cls.residente = perfil("resi", Rol.RESIDENTE)
        cls.vecino = perfil("vecino", Rol.RESIDENTE)
        cls.gestor = perfil("gestor", Rol.GESTOR)
        cls.unidad.dueno = cls.residente
        cls.unidad.save()
        cls.unidad2.dueno = cls.vecino
        cls.unidad2.save()

    def avisos(self, perfil):
        return list(Notification.objects.filter(recipient=perfil.user).values_list("message", flat=True))

    def commit(self):
        return self.captureOnCommitCallbacks(execute=True)


class GastosComunesTests(BaseNotis):
    def setUp(self):
        self.periodo = Periodo.objects.create(condominio=self.condo, anio=2026, mes=11,
                                              vencimiento=timezone.localdate() + timedelta(days=10),
                                              creado_por=self.gestor)
        self.liq = Liquidacion.objects.create(periodo=self.periodo, unidad=self.unidad,
                                              alicuota_usada=Decimal("50"), total=Decimal("45000"))

    def test_publicar_periodo_avisa_a_residentes_con_liquidacion(self):
        with self.commit():
            publicar_periodo(self.periodo)
        aviso = self.avisos(self.residente)
        self.assertEqual(len(aviso), 1)
        self.assertIn("11/2026", aviso[0])
        self.assertEqual(self.avisos(self.vecino), [])  # sin liquidación en este período
        with self.commit():  # guardar de nuevo no repite el aviso
            self.periodo.save()
        self.assertEqual(len(self.avisos(self.residente)), 1)

    def test_resultado_del_pago_online(self):
        pago = PagoOnline.objects.create(liquidacion=self.liq, usuario=self.residente, monto=45000)
        with self.commit():
            pago.estado = PagoOnline.Estado.PENDIENTE
            pago.save()
        self.assertEqual(self.avisos(self.residente), [])  # pendiente no avisa
        with self.commit():
            pago.estado = PagoOnline.Estado.APROBADO
            pago.save()
        self.assertIn("$45.000", self.avisos(self.residente)[0])
        self.assertIn("aprobado", self.avisos(self.residente)[0])

        rechazado = PagoOnline.objects.create(liquidacion=self.liq, usuario=self.residente, monto=1000)
        with self.commit():
            rechazado.estado = PagoOnline.Estado.RECHAZADO
            rechazado.save()
        self.assertTrue(any("rechazado" in m for m in self.avisos(self.residente)))


class AnunciosResidentesTests(BaseNotis):
    def test_moderacion(self):
        with self.commit():
            anuncio = AnuncioResidente.objects.create(condominio=self.condo, autor=self.residente,
                                                      unidad=self.unidad, titulo="Vendo bicicleta",
                                                      descripcion="Casi nueva")
        self.assertTrue(any("espera tu revisión" in m for m in self.avisos(self.gestor)))
        self.assertEqual(self.avisos(self.residente), [])

        self.client.force_login(self.gestor.user)
        with self.commit():
            self.client.post(reverse("anuncios:moderar", args=[anuncio.pk, "publicar"]))
        anuncio.refresh_from_db()
        self.assertEqual(anuncio.estado, AnuncioResidente.Estado.PUBLICADO)
        self.assertTrue(any("fue aprobado" in m for m in self.avisos(self.residente)))


class ReservasTests(BaseNotis):
    def test_residente_cancela_avisa_al_staff_y_no_a_si_mismo(self):
        area = AreaComun.objects.create(condominio=self.condo, nombre="Quincho", reservable=True)
        inicio = timezone.now() + timedelta(days=2)
        r = Reservacion.objects.create(tipo_reserva=Reservacion.Tipo.AREA_COMUN, usuario=self.residente,
                                       unidad=self.unidad, area_comun=area, fecha_reserva=inicio,
                                       fin_reserva=inicio + timedelta(hours=1))
        Notification.objects.all().delete()
        self.client.force_login(self.residente.user)
        with self.commit():
            self.client.post(reverse("reservacion_cancelar", args=[r.pk]))
        r.refresh_from_db()
        self.assertEqual(r.estado, Reservacion.Estado.CANCELADO)
        self.assertEqual(self.avisos(self.residente), [])
        aviso = Notification.objects.get(recipient=self.gestor.user)
        self.assertIn("canceló la reservación", aviso.message)
        self.assertEqual(aviso.url, reverse("reservaciones_lista"))


class TareasProgramadasTests(BaseNotis):
    def test_invitacion_expirada(self):
        inv, _token = Invitacion.crear(email="nuevo@x.cl", rol=Rol.RESIDENTE, condominio=self.condo,
                                       invitado_por=self.gestor)
        Invitacion.objects.filter(pk=inv.pk).update(expira=timezone.now() - timedelta(minutes=1))
        with self.commit():
            r = tasks.avisar_invitaciones.apply().get()
        self.assertEqual(r["expiradas"], 1)
        self.assertTrue(any("expiró" in m for m in self.avisos(self.gestor)))
        with self.commit():  # la segunda corrida no repite
            self.assertEqual(tasks.avisar_invitaciones.apply().get()["expiradas"], 0)

    def test_mantencion_proxima(self):
        hoy = timezone.localdate()
        equipo = ElementoMantenimiento.objects.create(condominio=self.condo, categoria="Ascensores",
                                                      nombre="Ascensor A")
        RegistroMantenimiento.objects.create(elemento=equipo, fecha=hoy - timedelta(days=60),
                                             descripcion="viejo", proximo_mantenimiento=hoy + timedelta(days=1))
        RegistroMantenimiento.objects.create(elemento=equipo, fecha=hoy - timedelta(days=30),
                                             descripcion="último", proximo_mantenimiento=hoy + timedelta(days=2))
        lejano = ElementoMantenimiento.objects.create(condominio=self.condo, categoria="Bombas", nombre="Bomba")
        RegistroMantenimiento.objects.create(elemento=lejano, fecha=hoy, descripcion="x",
                                             proximo_mantenimiento=hoy + timedelta(days=20))
        with self.commit():
            self.assertEqual(tasks.avisar_mantenciones_proximas.apply().get(), 1)
        aviso = self.avisos(self.gestor)
        self.assertEqual(len(aviso), 1)
        self.assertIn("Ascensor A programada para en 2 días", aviso[0])
        with self.commit():  # ya avisada: no se repite
            self.assertEqual(tasks.avisar_mantenciones_proximas.apply().get(), 0)

    def test_beat_apunta_a_tareas_registradas(self):
        from django.conf import settings
        from sol.celery import app

        app.loader.import_default_modules()
        for entrada in settings.CELERY_BEAT_SCHEDULE.values():
            self.assertIn(entrada["task"], app.tasks)

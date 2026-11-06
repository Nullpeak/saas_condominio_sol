from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
from .models import AreaComun, Ciudad, Condominio, Edificio, Reservacion, Unidad, Usuario


class ReservacionesListaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Santiago")
        cls.condominio = Condominio.objects.create(
            nombre="Condominio de prueba",
            direccion="Calle 1",
            ciudad=ciudad,
            edificios=1,
        )
        edificio = Edificio.objects.get(condominio=cls.condominio)
        cls.unidad_una = Unidad.objects.create(
            edificio=edificio,
            piso=1,
            posicion=1,
            numero_unidad="101",
        )
        cls.unidad_dos = Unidad.objects.create(
            edificio=edificio,
            piso=1,
            posicion=2,
            numero_unidad="102",
        )
        cls.area = AreaComun.objects.create(
            condominio=cls.condominio,
            nombre="Quincho",
            reservable=True,
        )
        User = get_user_model()
        cls.auth_una = User.objects.create_user(username="residente-1", password="test-pass")
        cls.residente_una = Usuario.objects.create(
            user=cls.auth_una,
            condominio=cls.condominio,
            nombre="Ana",
            apellido="Uno",
            email="ana@example.test",
            rol=Usuario.Rol.RESIDENTE,
        )
        cls.auth_dos = User.objects.create_user(username="residente-2", password="test-pass")
        cls.residente_dos = Usuario.objects.create(
            user=cls.auth_dos,
            condominio=cls.condominio,
            nombre="Beto",
            apellido="Dos",
            email="beto@example.test",
            rol=Usuario.Rol.RESIDENTE,
        )

    def crear_reserva(self, residente, unidad, inicio, fin, estado=Reservacion.Estado.PENDIENTE):
        return Reservacion.objects.create(
            tipo_reserva=Reservacion.Tipo.AREA_COMUN,
            usuario=residente,
            unidad=unidad,
            area_comun=self.area,
            fecha_reserva=inicio,
            fin_reserva=fin,
            estado=estado,
        )

    def test_residente_solo_ve_sus_reservas_activas_y_puede_abrir_historial(self):
        ahora = timezone.now()
        propia = self.crear_reserva(
            self.residente_una,
            self.unidad_una,
            ahora + timedelta(days=1),
            ahora + timedelta(days=1, hours=1),
        )
        ajena = self.crear_reserva(
            self.residente_dos,
            self.unidad_dos,
            ahora + timedelta(days=2),
            ahora + timedelta(days=2, hours=1),
        )
        vencida = self.crear_reserva(
            self.residente_una,
            self.unidad_una,
            ahora - timedelta(days=2),
            ahora - timedelta(days=2) + timedelta(hours=1),
        )
        cancelada = self.crear_reserva(
            self.residente_una,
            self.unidad_una,
            ahora + timedelta(days=3),
            ahora + timedelta(days=3, hours=1),
            Reservacion.Estado.CANCELADO,
        )

        self.client.force_login(self.auth_una)
        proximas = self.client.get(reverse("reservaciones_lista"))
        self.assertContains(proximas, "Mis reservas")
        self.assertEqual([reserva.pk for reserva in proximas.context["reservaciones"]], [propia.pk])
        self.assertNotContains(proximas, "Todas")
        self.assertEqual(
            Reservacion.objects.get(pk=vencida.pk).estado,
            Reservacion.Estado.COMPLETADO,
        )

        historial = self.client.get(reverse("reservaciones_lista"), {"filtro": "historial"})
        self.assertEqual(
            {reserva.pk for reserva in historial.context["reservaciones"]},
            {vencida.pk, cancelada.pk},
        )


class ReportesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Concepción")
        cls.condominio = Condominio.objects.create(
            nombre="Condominio autorizado",
            direccion="Calle 10",
            ciudad=ciudad,
            edificios=1,
        )
        cls.otro_condominio = Condominio.objects.create(
            nombre="Condominio ajeno",
            direccion="Calle 20",
            ciudad=ciudad,
            edificios=1,
        )
        edificio = Edificio.objects.get(condominio=cls.condominio)
        edificio_ajeno = Edificio.objects.get(condominio=cls.otro_condominio)
        unidad = Unidad.objects.create(edificio=edificio, piso=1, posicion=1, numero_unidad="301")
        unidad_ajena = Unidad.objects.create(edificio=edificio_ajeno, piso=1, posicion=1, numero_unidad="901")
        cls.area = AreaComun.objects.create(condominio=cls.condominio, nombre="Piscina", reservable=True)
        User = get_user_model()
        auth_admin = User.objects.create_user(username="admin-reportes", password="test-pass")
        cls.admin = Usuario.objects.create(
            user=auth_admin,
            condominio=cls.condominio,
            nombre="Admin",
            apellido="Reporte",
            email="admin-reportes@example.test",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        auth_residente = User.objects.create_user(username="residente-reporte", password="test-pass")
        residente = Usuario.objects.create(
            user=auth_residente,
            condominio=cls.condominio,
            nombre="Residente",
            apellido="Reporte",
            email="residente-reportes@example.test",
            rol=Usuario.Rol.RESIDENTE,
        )
        auth_residente_ajeno = User.objects.create_user(username="residente-ajeno", password="test-pass")
        residente_ajeno = Usuario.objects.create(
            user=auth_residente_ajeno,
            condominio=cls.otro_condominio,
            nombre="Residente",
            apellido="Ajeno",
            email="residente-ajeno@example.test",
            rol=Usuario.Rol.RESIDENTE,
        )
        inicio = timezone.now() + timedelta(hours=1)
        Reservacion.objects.create(
            tipo_reserva=Reservacion.Tipo.AREA_COMUN,
            usuario=residente,
            unidad=unidad,
            area_comun=cls.area,
            fecha_reserva=inicio,
            fin_reserva=inicio + timedelta(hours=1),
        )
        area_ajena = AreaComun.objects.create(
            condominio=cls.otro_condominio,
            nombre="Gimnasio",
            reservable=True,
        )
        Reservacion.objects.create(
            tipo_reserva=Reservacion.Tipo.AREA_COMUN,
            usuario=residente_ajeno,
            unidad=unidad_ajena,
            area_comun=area_ajena,
            fecha_reserva=inicio,
            fin_reserva=inicio + timedelta(hours=1),
        )
        elemento = ElementoMantenimiento.objects.create(
            condominio=cls.condominio,
            categoria="Ascensores",
            nombre="Ascensor torre A",
        )
        RegistroMantenimiento.objects.create(
            elemento=elemento,
            fecha=timezone.localdate(),
            descripcion="Mantención preventiva",
        )

    def setUp(self):
        self.client.force_login(self.admin.user)

    def test_reporte_reservas_filtra_por_permisos_y_exporta_csv(self):
        respuesta = self.client.get(reverse("reportes"), {"tipo": "reservas"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["total"], 1)
        self.assertEqual(respuesta.context["resumen"][0]["area_comun__nombre"], "Piscina")

        csv = self.client.get(reverse("reportes"), {"tipo": "reservas", "exportar": "csv"})
        self.assertEqual(csv.status_code, 200)
        self.assertIn("Piscina", csv.content.decode("utf-8-sig"))
        self.assertNotIn("Gimnasio", csv.content.decode("utf-8-sig"))

    def test_reporte_mantenimiento_aplica_categoria_y_exporta_csv(self):
        respuesta = self.client.get(reverse("reportes"), {"tipo": "mantenimiento", "categoria": "Ascensores"})
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.context["total"], 1)
        self.assertEqual(respuesta.context["resumen"][0]["elemento__categoria"], "Ascensores")

        csv = self.client.get(reverse("reportes"), {"tipo": "mantenimiento", "categoria": "Ascensores", "exportar": "csv"})
        self.assertIn("Ascensor torre A", csv.content.decode("utf-8-sig"))


class SelectorAreasComunesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="La Serena")
        cls.condominio_uno = Condominio.objects.create(
            nombre="Condominio Uno", direccion="Calle 1", ciudad=ciudad, edificios=1
        )
        cls.condominio_dos = Condominio.objects.create(
            nombre="Condominio Dos", direccion="Calle 2", ciudad=ciudad, edificios=1
        )
        cls.condominio_ajeno = Condominio.objects.create(
            nombre="Condominio Ajeno", direccion="Calle 3", ciudad=ciudad, edificios=1
        )
        auth_admin = get_user_model().objects.create_user(username="admin-areas", password="test-pass")
        cls.admin = Usuario.objects.create(
            user=auth_admin,
            condominio=cls.condominio_uno,
            nombre="Admin",
            apellido="Áreas",
            email="admin-areas@example.test",
            rol=Usuario.Rol.ADMINISTRADOR,
        )
        cls.admin.condominios_administrados.add(cls.condominio_dos)

    def setUp(self):
        self.client.force_login(self.admin.user)

    def test_menu_selector_muestra_tarjetas_solo_de_condominios_asignados(self):
        respuesta = self.client.get(reverse("areas_comunes_selector"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Condominio Uno")
        self.assertContains(respuesta, "Condominio Dos")
        self.assertNotContains(respuesta, "Condominio Ajeno")
        self.assertContains(respuesta, reverse("areas_comunes_lista", args=[self.condominio_uno.pk]))
        self.assertContains(respuesta, reverse("areas_comunes_lista", args=[self.condominio_dos.pk]))

    def test_crear_desde_un_condominio_mantiene_ese_condominio_asignado(self):
        url_lista = reverse("areas_comunes_lista", args=[self.condominio_dos.pk])
        listado = self.client.get(url_lista)
        self.assertContains(listado, "Condominio Dos")
        self.assertContains(listado, reverse("area_comun_crear", args=[self.condominio_dos.pk]))

        url_crear = reverse("area_comun_crear", args=[self.condominio_dos.pk])
        formulario = self.client.get(url_crear)
        self.assertContains(formulario, "Condominio Dos")
        self.assertNotContains(formulario, 'name="condominio"')
        respuesta = self.client.post(url_crear, {
            "condominio": self.condominio_uno.pk,
            "nombre": "Sala multiuso",
            "descripcion": "Área del segundo condominio",
        })

        self.assertRedirects(respuesta, url_lista)
        area = AreaComun.objects.get(nombre="Sala multiuso")
        self.assertEqual(area.condominio_id, self.condominio_dos.pk)

    def test_gestor_solo_recibe_tarjeta_de_su_condominio(self):
        auth_gestor = get_user_model().objects.create_user(username="gestor-areas", password="test-pass")
        Usuario.objects.create(
            user=auth_gestor,
            condominio=self.condominio_uno,
            nombre="Gestor",
            apellido="Áreas",
            email="gestor-areas@example.test",
            rol=Usuario.Rol.GESTOR,
        )
        self.client.force_login(auth_gestor)

        respuesta = self.client.get(reverse("areas_comunes_selector"))

        self.assertContains(respuesta, "Condominio Uno")
        self.assertNotContains(respuesta, "Condominio Dos")

from datetime import date

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from invitaciones.forms import AceptarInvitacionForm
from invitaciones.models import Invitacion
from launch.forms import CondominioForm, SuperUsuarioForm
from mantenimientos.models import ElementoMantenimiento
from visitas.models import Visita

from .models import Ciudad, Condominio, Edificio, Unidad, Usuario
from .validators import normalizar_email, normalizar_nombre_propio, normalizar_patente


class FuncionesNormalizacionTests(SimpleTestCase):
    def test_nombres(self):
        casos = {
            "jose luis ignacio": "Jose Luis Ignacio",
            "  QUELOPANA   pinto ": "Quelopana Pinto",
            "DE LA FUENTE": "De la Fuente",
            "maría del carmen": "María del Carmen",
            "maría-josé": "María-José",
            "o'higgins": "O'Higgins",
            "": "",
        }
        for entrada, esperado in casos.items():
            self.assertEqual(normalizar_nombre_propio(entrada), esperado, entrada)

    def test_email_y_patente(self):
        self.assertEqual(normalizar_email("  Juan.Perez@Mail.CL "), "juan.perez@mail.cl")
        self.assertEqual(normalizar_patente("ab-cd 12"), "ABCD12")


class NormalizacionModelosTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Santiago")
        cls.condominio = Condominio.objects.create(
            nombre="  Los   Robles ", direccion="Calle  1", ciudad=ciudad, edificios=1,
            estimado_personas_departamento=4,
        )
        cls.edificio = Edificio.objects.get(condominio=cls.condominio)
        cls.unidad = Unidad.objects.create(edificio=cls.edificio, piso=1, posicion=1, numero_unidad="101", cantidad_residentes=6)

    def test_usuario_guarda_nombres_y_correo_normalizados(self):
        u = Usuario.objects.create(nombre="jose luis ignacio", apellido="quelopana", apellido2="PINTO",
                                   email="Jose@Mail.CL", rut="7.654.321-6")
        u.refresh_from_db()
        self.assertEqual((u.nombre, u.apellido, u.apellido2), ("Jose Luis Ignacio", "Quelopana", "Pinto"))
        self.assertEqual(u.email, "jose@mail.cl")
        self.assertEqual(u.rut, "7654321-6")

    def test_condominio_espacios(self):
        self.condominio.refresh_from_db()
        self.assertEqual((self.condominio.nombre, self.condominio.direccion), ("Los Robles", "Calle 1"))

    def test_visita_nombre_y_patente(self):
        v = Visita.objects.create(condominio=self.condominio, unidad=self.unidad, nombre="pedro  soto",
                                  patente_vehiculo="ab-cd-12", fecha_visita=date.today())
        v.refresh_from_db()
        self.assertEqual((v.nombre, v.patente_vehiculo), ("Pedro Soto", "ABCD12"))

    def test_mantencion_valida_y_formatea_telefono(self):
        e = ElementoMantenimiento(condominio=self.condominio, categoria="Ascensor", nombre="Ascensor  norte",
                                  telefono_actual="+56 9 7391 1798", correo_actual="Soporte@Empresa.CL")
        e.full_clean()
        self.assertEqual((e.telefono_actual, e.correo_actual, e.nombre), ("+56973911798", "soporte@empresa.cl", "Ascensor norte"))
        e.telefono_actual = "123"
        with self.assertRaises(Exception) as ctx:
            e.full_clean()
        self.assertIn("telefono_actual", ctx.exception.message_dict)

    def test_invitacion_detecta_rut_duplicado_escrito_con_puntos(self):
        Usuario.objects.create(nombre="A", apellido="B", email="a@b.cl", rut="7654321-6")
        inv = Invitacion(email="nuevo@x.cl", rol=Usuario.Rol.RESIDENTE, condominio=self.condominio)
        form = AceptarInvitacionForm({
            "username": "nuevo", "nombre": "ana  maria", "apellido": "DE LA FUENTE", "rut": "7.654.321-6",
            "telefono": "", "password1": "Clave-Muy-Segura-91", "password2": "Clave-Muy-Segura-91",
        }, invitacion=inv)
        self.assertFalse(form.is_valid())
        self.assertIn("rut", form.errors)
        self.assertEqual(form.cleaned_data["nombre"], "Ana Maria")
        self.assertEqual(form.cleaned_data["apellido"], "De la Fuente")

    def test_launch_normaliza(self):
        form = SuperUsuarioForm({
            "username": "admin", "nombre": "marcelo", "apellido": "PIZARRO", "email": "Marcelo@Gmail.com",
            "rut": "7.654.321-6", "telefono": "+56 9 7391 1798",
            "password1": "Clave-Muy-Segura-91", "password2": "Clave-Muy-Segura-91",
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(
            (form.cleaned_data["nombre"], form.cleaned_data["apellido"], form.cleaned_data["email"], form.cleaned_data["rut"]),
            ("Marcelo", "Pizarro", "marcelo@gmail.com", "7654321-6"),
        )

    # ---------- Estimado de personas ----------
    def test_estimado_es_aviso_no_limite(self):
        self.assertTrue(self.unidad.excede_estimado)
        self.unidad.cantidad_residentes = 4
        self.assertFalse(self.unidad.excede_estimado)

    def test_launch_acepta_estimado_opcional(self):
        base = {"nombre": "X", "direccion": "Y", "ciudad": "Santiago", "edificios": 1, "numero_pisos": 3, "viviendas_por_piso": 2}
        self.assertTrue(CondominioForm(base).is_valid())
        self.assertTrue(CondominioForm({**base, "estimado_personas_departamento": 4}).is_valid())
        self.assertFalse(CondominioForm({**base, "estimado_personas_departamento": 50}).is_valid())

    def test_editar_condominio_guarda_estimado_y_aviso_en_unidades(self):
        user = get_user_model().objects.create_user("super", password="x")
        Usuario.objects.create(user=user, nombre="S", apellido="A", email="s@a.cl", rol=Usuario.Rol.SUPER)
        self.client.force_login(user)
        r = self.client.post(reverse("condominio_editar", args=[self.condominio.id]), {
            "nombre": "Los Robles", "direccion": "Calle 1", "ciudad": self.condominio.ciudad_id, "edificios": 1,
            "activo": "on", "modalidad_mora": "reglamento_tipo", "estimado_personas_departamento": "3",
        })
        self.assertEqual(r.status_code, 302)
        self.condominio.refresh_from_db()
        self.assertEqual(self.condominio.estimado_personas_departamento, 3)
        r = self.client.get(reverse("unidades_lista", args=[self.edificio.id]))
        self.assertContains(r, "Sobre el estimado")

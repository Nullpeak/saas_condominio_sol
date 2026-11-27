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


class CrearCondominioTests(TestCase):
    """Crear un condominio desde la app debe dejar lo mismo que el launch: edificios y unidades."""

    def setUp(self):
        user = get_user_model().objects.create_user("admin2", password="x")
        Ciudad.objects.create(nombre="Santiago")
        otro = Condominio.objects.create(nombre="Base", direccion="X", ciudad=Ciudad.objects.get(), edificios=1)
        self.admin = Usuario.objects.create(user=user, nombre="A", apellido="B", email="a2@x.cl",
                                            rol=Usuario.Rol.ADMINISTRADOR, condominio=otro)
        self.client.force_login(user)

    def test_crea_edificios_y_unidades_como_el_launch(self):
        r = self.client.post(reverse("condominio_crear"), {
            "nombre": "  Los   Aromos ", "direccion": "Calle 2", "ciudad": "santiago",
            "edificios": "2", "numero_pisos": "3", "viviendas_por_piso": "4", "estimado_personas_departamento": "4",
        })
        condominio = Condominio.objects.get(nombre="Los Aromos")
        self.assertRedirects(r, reverse("edificios_lista", args=[condominio.pk]))
        self.assertEqual(condominio.ciudad.nombre, "Santiago")           # reutiliza la ciudad, no duplica
        self.assertEqual(condominio.estimado_personas_departamento, 4)
        edificios = list(condominio.edificios_set.order_by("numero"))
        self.assertEqual([(e.numero_pisos, e.viviendas_por_piso) for e in edificios], [(3, 4), (3, 4)])
        self.assertEqual(Unidad.objects.filter(edificio__condominio=condominio).count(), 24)
        self.assertTrue(Unidad.objects.filter(edificio=edificios[1], numero_unidad="2-34").exists())
        self.assertTrue(self.admin.condominios_administrados.filter(pk=condominio.pk).exists())

    def test_valida_pisos_y_campos_obligatorios(self):
        r = self.client.post(reverse("condominio_crear"), {"nombre": "X", "direccion": "", "ciudad": "Santiago",
                                                           "edificios": "1", "numero_pisos": "40", "viviendas_por_piso": "2"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Condominio.objects.filter(nombre="X").exists())
        self.assertContains(r, "is-invalid")

    def test_pagina_muestra_campos_del_launch(self):
        r = self.client.get(reverse("condominio_crear"))
        for texto in ("Pisos por edificio", "Viviendas por piso", "lista-ciudades", "Estimado de personas"):
            self.assertContains(r, texto)


class LaunchTests(TestCase):
    def test_launch_sigue_creando_todo_con_el_servicio_compartido(self):
        from django.urls import NoReverseMatch
        try:
            url = reverse("launch:launch")
        except NoReverseMatch:
            url = reverse("launch")
        r = self.client.post(url, {
            "u-username": "dueno", "u-nombre": "marcelo", "u-apellido": "pizarro", "u-email": "M@x.cl",
            "u-rut": "7.654.321-6", "u-telefono": "+56 9 7391 1798",
            "u-password1": "Clave-Muy-Segura-91", "u-password2": "Clave-Muy-Segura-91",
            "c-nombre": "Portal del Sol", "c-direccion": "Calle 1", "c-ciudad": "Santiago",
            "c-edificios": "1", "c-numero_pisos": "2", "c-viviendas_por_piso": "3",
        })
        self.assertEqual(r.status_code, 302)
        condominio = Condominio.objects.get(nombre="Portal del Sol")
        self.assertEqual(Unidad.objects.filter(edificio__condominio=condominio).count(), 6)
        self.assertEqual(Usuario.objects.get(rol=Usuario.Rol.SUPER).nombre, "Marcelo")


import shutil as _shutil, tempfile as _tempfile
from django.test import override_settings as _override

_MEDIA = _tempfile.mkdtemp()


@_override(MEDIA_ROOT=_MEDIA)
class PortadaCondominioTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        _shutil.rmtree(_MEDIA, ignore_errors=True)

    def test_portada_se_ve_por_la_vista_protegida(self):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        User = get_user_model()
        ciudad = Ciudad.objects.create(nombre="Santiago")
        base = Condominio.objects.create(nombre="Base", direccion="X", ciudad=ciudad, edificios=1)
        admin = Usuario.objects.create(user=User.objects.create_user("a", password="x"), nombre="A", apellido="B",
                                       email="a@x.cl", rol=Usuario.Rol.ADMINISTRADOR, condominio=base)
        ajeno = Usuario.objects.create(user=User.objects.create_user("b", password="x"), nombre="C", apellido="D",
                                       email="b@x.cl", rol=Usuario.Rol.ADMINISTRADOR, condominio=base)
        img = BytesIO(); Image.new("RGB", (40, 20), "teal").save(img, "JPEG")
        self.client.force_login(admin.user)
        self.client.post(reverse("condominio_crear"), {
            "nombre": "Con Foto", "direccion": "Calle", "ciudad": "Santiago", "edificios": "1",
            "numero_pisos": "2", "viviendas_por_piso": "1",
            "portada": SimpleUploadedFile("portada.jpg", img.getvalue(), content_type="image/jpeg"),
        })
        condominio = Condominio.objects.get(nombre="Con Foto")
        url = reverse("portada_condominio", args=[condominio.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Content-Type"], "image/jpeg")
        self.assertContains(self.client.get(reverse("condominios_lista")), url)
        # Un administrador sin acceso a ese condominio no la ve
        self.client.force_login(ajeno.user)
        self.assertEqual(self.client.get(url).status_code, 404)
        # Sin sesión, al login
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)


@_override(MEDIA_ROOT=_MEDIA)
class ValidacionPortadaTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.ciudad = Ciudad.objects.create(nombre="Santiago")
        self.condominio = Condominio.objects.create(nombre="Base", direccion="X", ciudad=self.ciudad, edificios=1)
        su = Usuario.objects.create(user=User.objects.create_user("su", password="x"), nombre="S", apellido="U",
                                    email="su@x.cl", rol=Usuario.Rol.SUPER)
        self.client.force_login(su.user)

    def _falsa(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile("foto.jpg", b"%PDF-1.4 esto no es una imagen", content_type="image/jpeg")

    def test_editar_rechaza_archivo_que_no_es_imagen(self):
        r = self.client.post(reverse("condominio_editar", args=[self.condominio.pk]), {
            "nombre": "Base", "direccion": "X", "ciudad": self.ciudad.pk, "edificios": "1", "activo": "on",
            "modalidad_mora": "reglamento_tipo", "portada": self._falsa(),
        })
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "dañada o no es válida")
        self.condominio.refresh_from_db()
        self.assertFalse(self.condominio.portada)

    def test_crear_rechaza_archivo_que_no_es_imagen(self):
        r = self.client.post(reverse("condominio_crear"), {
            "nombre": "Nuevo", "direccion": "Y", "ciudad": "Santiago", "edificios": "1",
            "numero_pisos": "2", "viviendas_por_piso": "1", "portada": self._falsa(),
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Condominio.objects.filter(nombre="Nuevo").exists())

    def test_extension_no_permitida(self):
        from django.core.exceptions import ValidationError
        from django.core.files.uploadedfile import SimpleUploadedFile
        from core.validators import validar_imagen
        with self.assertRaises(ValidationError):
            validar_imagen(SimpleUploadedFile("script.svg", b"<svg/>"))


@_override(MEDIA_ROOT=_MEDIA)
class NombresDeImagenesTests(TestCase):
    def _img(self, nombre="foto.png", modo="RGBA", tam=(3000, 2000)):
        from io import BytesIO
        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image
        b = BytesIO()
        Image.new(modo, tam, (10, 120, 100, 128) if modo == "RGBA" else "teal").save(b, "PNG" if nombre.endswith("png") else "JPEG")
        return SimpleUploadedFile(nombre, b.getvalue())

    def test_portada_queda_como_portada_nombre_jpg(self):
        from PIL import Image
        ciudad = Ciudad.objects.create(nombre="Santiago")
        c = Condominio.objects.create(nombre="Portal del Sol", direccion="X", ciudad=ciudad, edificios=1, portada=self._img())
        self.assertEqual(c.portada.name, "condominios/portadas/portada_portal_del_sol.jpg")
        im = Image.open(c.portada.path)
        self.assertEqual((im.format, max(im.size)), ("JPEG", 1920))
        # Reemplazarla mantiene el nombre exacto y no deja la anterior en el disco
        c.portada = self._img("otra.jpg", modo="RGB", tam=(800, 600))
        c.save()
        self.assertEqual(c.portada.name, "condominios/portadas/portada_portal_del_sol.jpg")
        self.assertEqual(Image.open(c.portada.path).size, (800, 600))

    def test_avatar_queda_como_avatar_jpg_y_avisa_el_cambio(self):
        from notis.models import Notification
        User = get_user_model()
        ciudad = Ciudad.objects.create(nombre="Stgo")
        condo = Condominio.objects.create(nombre="C", direccion="X", ciudad=ciudad, edificios=1)
        Usuario.objects.create(user=User.objects.create_user("adm", password="x"), nombre="A", apellido="D",
                               email="adm@x.cl", rol=Usuario.Rol.ADMINISTRADOR, condominio=condo)
        u = Usuario.objects.create(user=User.objects.create_user("juan", password="x"), nombre="Juan", apellido="P",
                                   email="j@x.cl", condominio=condo)
        u.avatar = self._img(); u.save()
        self.assertEqual(u.avatar.name, "user_images/juan/avatar.jpg")
        with self.captureOnCommitCallbacks(execute=True):
            u = Usuario.objects.get(pk=u.pk)
            u.avatar = self._img("nueva.jpg", modo="RGB", tam=(300, 300)); u.save()
        self.assertEqual(u.avatar.name, "user_images/juan/avatar.jpg")
        self.assertTrue(Notification.objects.filter(message__contains="imagen de perfil").exists())

    def test_navegador_revalida_y_recibe_304_si_no_cambio(self):
        User = get_user_model()
        u = Usuario.objects.create(user=User.objects.create_user("ana", password="x"), nombre="Ana", apellido="P", email="a@x.cl")
        u.avatar = self._img(); u.save()
        self.client.force_login(u.user)
        url = reverse("avatar_usuario", args=[u.pk])
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Cache-Control"], "private, no-cache")
        r2 = self.client.get(url, HTTP_IF_MODIFIED_SINCE=r["Last-Modified"])
        self.assertEqual(r2.status_code, 304)


class DesactivarEdificioTests(TestCase):
    def setUp(self):
        User = get_user_model()
        ciudad = Ciudad.objects.create(nombre="Santiago")
        self.condominio = Condominio.objects.create(nombre="C", direccion="X", ciudad=ciudad, edificios=1)
        self.edificio = Edificio.objects.get(condominio=self.condominio)
        self.edificio.numero_pisos = 2
        self.edificio.viviendas_por_piso = 2
        self.edificio.save()  # genera 4 unidades
        su = Usuario.objects.create(user=User.objects.create_user("su", password="x"), nombre="S", apellido="U",
                                    email="su@x.cl", rol=Usuario.Rol.SUPER)
        self.client.force_login(su.user)
        self.url = reverse("edificio_cambiar_estado", args=[self.edificio.pk])

    def activas(self):
        return self.edificio.unidades.filter(activo=True).count()

    def test_desactivar_edificio_desactiva_sus_unidades_y_reactivar_las_devuelve(self):
        # Una unidad ya estaba desactivada a mano
        a_mano = self.edificio.unidades.order_by("pk").first()
        self.client.post(reverse("unidad_cambiar_estado", args=[a_mano.pk]))
        self.assertEqual(self.activas(), 3)

        self.client.post(self.url)
        self.edificio.refresh_from_db()
        self.assertFalse(self.edificio.activo)
        self.assertEqual(self.activas(), 0)

        self.client.post(self.url)
        self.edificio.refresh_from_db()
        self.assertTrue(self.edificio.activo)
        self.assertEqual(self.activas(), 3)          # vuelven las 3 que apagó el edificio
        a_mano.refresh_from_db()
        self.assertFalse(a_mano.activo)               # la desactivada a mano sigue inactiva

    def test_con_edificio_inactivo_no_se_puede_activar_una_unidad(self):
        self.client.post(self.url)
        u = self.edificio.unidades.first()
        self.client.post(reverse("unidad_cambiar_estado", args=[u.pk]))
        u.refresh_from_db()
        self.assertFalse(u.activo)

    def test_pide_confirmacion_si_tiene_unidades_activas(self):
        r = self.client.get(reverse("edificios_lista", args=[self.condominio.pk]))
        self.assertContains(r, "junto con sus 4 unidades activas")

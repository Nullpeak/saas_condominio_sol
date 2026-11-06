from django.core import mail
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest import mock

from core.enlaces import base_publica, url_publica
from core.models import Usuario


class EnlacesTests(TestCase):
    def req(self, host):
        return RequestFactory().get("/", HTTP_HOST=host)

    @mock.patch("core.enlaces.ip_red_local", return_value="192.168.1.25")
    def test_localhost_se_cambia_por_ip_de_red(self, _):
        self.assertEqual(base_publica(self.req("127.0.0.1:8000")), ("http", "192.168.1.25:8000"))
        self.assertEqual(base_publica(self.req("localhost:8000")), ("http", "192.168.1.25:8000"))
        self.assertEqual(base_publica(self.req("192.168.1.30:8000")), ("http", "192.168.1.30:8000"))

    @override_settings(SITIO_URL="https://condominio.cl")
    def test_sitio_url_manda(self):
        self.assertEqual(url_publica(self.req("localhost:8000"), "/x/"), "https://condominio.cl/x/")

    @mock.patch("core.enlaces.ip_red_local", return_value="192.168.1.25")
    def test_correo_de_recuperacion_usa_ip(self, _):
        u = get_user_model().objects.create_user("ana", "ana@x.cl", "clave-segura-123")
        Usuario.objects.create(user=u, nombre="Ana", apellido="T", email="ana@x.cl", rol=Usuario.Rol.RESIDENTE)
        self.client.post(reverse("password_reset_request"), {"email": "ana@x.cl"}, HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("http://192.168.1.25:8000/", mail.outbox[0].body)
        self.assertNotIn("127.0.0.1", mail.outbox[0].body)

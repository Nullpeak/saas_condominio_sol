from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Ciudad, Condominio, Edificio, Unidad, Usuario
from .models import AnuncioResidente


class CrearAnuncioResidenteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Valparaíso")
        cls.condominio = Condominio.objects.create(
            nombre="Condominio de prueba",
            direccion="Avenida 2",
            ciudad=ciudad,
            edificios=1,
        )
        edificio = Edificio.objects.get(condominio=cls.condominio)
        cls.unidad_una = Unidad.objects.create(
            edificio=edificio,
            piso=1,
            posicion=1,
            numero_unidad="201",
        )
        cls.unidad_dos = Unidad.objects.create(
            edificio=edificio,
            piso=1,
            posicion=2,
            numero_unidad="202",
        )
        User = get_user_model()
        cls.auth_user = User.objects.create_user(username="residente", password="test-pass")
        cls.residente = Usuario.objects.create(
            user=cls.auth_user,
            condominio=cls.condominio,
            nombre="Cami",
            apellido="Prueba",
            email="cami@example.test",
            rol=Usuario.Rol.RESIDENTE,
        )
        cls.unidad_una.dueno = cls.residente
        cls.unidad_una.save(update_fields=["dueno"])
        cls.unidad_dos.dueno = cls.residente
        cls.unidad_dos.save(update_fields=["dueno"])

    def test_formulario_no_expone_selector_de_unidad_y_servidor_usa_unidad_asignada(self):
        self.client.force_login(self.auth_user)
        url = reverse("anuncios:crear")
        formulario = self.client.get(url, {"tipo": "residentes"})
        self.assertContains(formulario, "Unidad asociada")
        self.assertContains(formulario, "201")
        self.assertNotContains(formulario, 'name="unidad"')
        self.assertNotContains(formulario, 'id="unidad"')

        respuesta = self.client.post(url, {
            "tipo": "residentes",
            "unidad": self.unidad_dos.pk,
            "titulo": "Aviso de prueba",
            "descripcion": "Contenido del aviso.",
            "alcance": AnuncioResidente.Alcance.EDIFICIO,
        })

        self.assertRedirects(respuesta, reverse("anuncios:lista"))
        anuncio = AnuncioResidente.objects.get(titulo="Aviso de prueba")
        self.assertEqual(anuncio.unidad_id, self.unidad_una.pk)

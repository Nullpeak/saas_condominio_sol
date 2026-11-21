import shutil
import tempfile
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from notis.models import Notification

from .models import Ciudad, Condominio, Edificio, SolicitudBajaArrendatario, Unidad, Usuario

MEDIA_TMP = tempfile.mkdtemp()


def imagen_png():
    salida = BytesIO()
    Image.new("RGB", (900, 600), "teal").save(salida, format="PNG")
    return SimpleUploadedFile("foto.png", salida.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class CuentaTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        ciudad = Ciudad.objects.create(nombre="Santiago")
        cls.condominio = Condominio.objects.create(nombre="Los Robles", direccion="Calle 1", ciudad=ciudad, edificios=1)
        edificio = Edificio.objects.get(condominio=cls.condominio)

        def perfil(username, rol, email):
            user = User.objects.create_user(username=username, password="Clave-segura-123", email=email)
            return Usuario.objects.create(user=user, condominio=cls.condominio, nombre=username, apellido="Prueba", rol=rol, email=email)

        cls.admin = perfil("admin", Usuario.Rol.ADMINISTRADOR, "admin@x.cl")
        cls.dueno = perfil("dueno", Usuario.Rol.RESIDENTE, "dueno@x.cl")
        cls.arrendatario = perfil("arrendatario", Usuario.Rol.RESIDENTE, "arr@x.cl")
        cls.otro = perfil("otro", Usuario.Rol.RESIDENTE, "otro@x.cl")
        cls.unidad = Unidad.objects.create(
            edificio=edificio, piso=1, posicion=1, numero_unidad="101",
            dueno=cls.dueno, arrendatario=cls.arrendatario, propietario_vive=False, cantidad_residentes=3,
        )

    def entrar(self, perfil):
        self.client.force_login(perfil.user)

    # ---------- Mis datos ----------
    def test_mis_datos_actualiza_todo_y_foto_menos_rut(self):
        self.entrar(self.dueno)
        r = self.client.post(reverse("mis_datos"), {
            "accion": "datos", "nombre": "juan", "nombre2": "", "apellido": "pérez", "apellido2": "soto",
            "email": "Nuevo@X.cl", "telefono": "", "rut": "11.111.111-1", "avatar": imagen_png(),
        })
        self.assertRedirects(r, reverse("mis_datos"))
        self.dueno.refresh_from_db()
        self.assertEqual((self.dueno.nombre, self.dueno.apellido2, self.dueno.email), ("Juan", "Soto", "nuevo@x.cl"))
        self.assertEqual(self.dueno.telefono, "")
        self.assertIsNone(self.dueno.rut)  # el RUT no se edita desde aquí
        self.assertEqual(self.dueno.user.email, "nuevo@x.cl")
        self.assertTrue(self.dueno.avatar.name.endswith(".jpg"))
        self.assertLessEqual(max(Image.open(self.dueno.avatar.path).size), 512)

        # Sirve la foto a un vecino del condominio y la página renderiza
        self.assertEqual(self.client.get(reverse("avatar_usuario", args=[self.dueno.id])).status_code, 200)
        self.assertContains(self.client.get(reverse("mis_datos")), "Quitar foto")

        # Quitar foto
        self.client.post(reverse("mis_datos"), {"accion": "quitar_foto"})
        self.dueno.refresh_from_db()
        self.assertFalse(self.dueno.avatar)

    def test_mis_datos_rechaza_correo_repetido(self):
        self.entrar(self.dueno)
        r = self.client.post(reverse("mis_datos"), {"accion": "datos", "nombre": "A", "apellido": "B", "email": "arr@x.cl"})
        self.assertContains(r, "ya está registrado")

    def test_cambiar_contrasena_mantiene_sesion(self):
        self.entrar(self.dueno)
        r = self.client.post(reverse("mis_datos"), {
            "accion": "password", "old_password": "Clave-segura-123",
            "new_password1": "OtraClave-456!", "new_password2": "OtraClave-456!",
        })
        self.assertRedirects(r, reverse("mis_datos"))
        self.assertEqual(self.client.get(reverse("mis_datos")).status_code, 200)

    # ---------- Mi unidad ----------
    def test_dueno_no_edita_residentes_si_hay_arrendatario(self):
        self.entrar(self.dueno)
        self.assertContains(self.client.get(reverse("mi_unidad")), "la actualiza tu arrendatario")
        self.client.post(reverse("mi_unidad"), {"accion": "residentes", "unidad": self.unidad.id, "cantidad_residentes": 5})
        self.unidad.refresh_from_db()
        self.assertEqual(self.unidad.cantidad_residentes, 3)

    def test_arrendatario_edita_residentes(self):
        self.entrar(self.arrendatario)
        self.client.post(reverse("mi_unidad"), {"accion": "residentes", "unidad": self.unidad.id, "cantidad_residentes": 4})
        self.unidad.refresh_from_db()
        self.assertEqual(self.unidad.cantidad_residentes, 4)

    def test_ajeno_no_toca_la_unidad(self):
        self.entrar(self.otro)
        self.client.post(reverse("mi_unidad"), {"accion": "solicitar_baja", "unidad": self.unidad.id, "motivo": "x"})
        self.assertFalse(SolicitudBajaArrendatario.objects.exists())

    # ---------- Flujo de baja ----------
    def solicitar(self):
        self.entrar(self.dueno)
        self.client.post(reverse("mi_unidad"), {"accion": "solicitar_baja", "unidad": self.unidad.id, "motivo": "Terminó el contrato"})
        return SolicitudBajaArrendatario.objects.get()

    def test_arrendatario_no_puede_pedir_baja(self):
        self.entrar(self.arrendatario)
        self.client.post(reverse("mi_unidad"), {"accion": "solicitar_baja", "unidad": self.unidad.id, "motivo": "x"})
        self.assertFalse(SolicitudBajaArrendatario.objects.exists())

    def test_aprobar_baja_desvincula_y_desactiva(self):
        with self.captureOnCommitCallbacks(execute=True):
            solicitud = self.solicitar()
        self.assertTrue(Notification.objects.filter(recipient=self.admin.user).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.arrendatario.user).exists())
        # No se puede duplicar
        self.client.post(reverse("mi_unidad"), {"accion": "solicitar_baja", "unidad": self.unidad.id, "motivo": "otra"})
        self.assertEqual(SolicitudBajaArrendatario.objects.count(), 1)
        # Un residente no puede resolver
        self.client.post(reverse("solicitud_baja_resolver", args=[solicitud.id]), {"accion": "aprobar"})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "pendiente")

        self.entrar(self.admin)
        self.assertContains(self.client.get(reverse("solicitudes_baja_lista")), "Terminó el contrato")
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("solicitud_baja_resolver", args=[solicitud.id]), {"accion": "aprobar", "comentario": "Conversado"})
        solicitud.refresh_from_db()
        self.unidad.refresh_from_db()
        self.arrendatario.refresh_from_db()
        self.assertEqual(solicitud.estado, "aprobada")
        self.assertEqual(solicitud.resuelto_por, self.admin)
        self.assertIsNone(self.unidad.arrendatario)
        self.assertIsNone(self.unidad.cantidad_residentes)
        self.assertFalse(self.arrendatario.activo)
        self.assertFalse(self.arrendatario.user.is_active)
        self.assertTrue(Notification.objects.filter(recipient=self.dueno.user, message__contains="aprobó").exists())

        # Ahora el dueño sí puede actualizar los residentes
        self.entrar(self.dueno)
        self.client.post(reverse("mi_unidad"), {"accion": "residentes", "unidad": self.unidad.id, "cantidad_residentes": 2})
        self.unidad.refresh_from_db()
        self.assertEqual(self.unidad.cantidad_residentes, 2)

    def test_rechazar_baja_no_cambia_nada(self):
        solicitud = self.solicitar()
        self.entrar(self.admin)
        self.client.post(reverse("solicitud_baja_resolver", args=[solicitud.id]), {"accion": "rechazar"})
        solicitud.refresh_from_db()
        self.unidad.refresh_from_db()
        self.assertEqual(solicitud.estado, "rechazada")
        self.assertEqual(self.unidad.arrendatario, self.arrendatario)

    def test_dueno_cancela_solicitud(self):
        self.solicitar()
        self.client.post(reverse("mi_unidad"), {"accion": "cancelar_baja", "unidad": self.unidad.id})
        self.assertEqual(SolicitudBajaArrendatario.objects.get().estado, "cancelada")

    def test_paginas_renderizan(self):
        for perfil, url in ((self.dueno, "mi_unidad"), (self.arrendatario, "mi_unidad"), (self.admin, "mis_datos"), (self.admin, "solicitudes_baja_lista")):
            self.entrar(perfil)
            self.assertEqual(self.client.get(reverse(url)).status_code, 200, url)


    # ---------- Ficha del residente en tickets ----------
    def test_ficha_usuario_en_ticket_solo_para_staff(self):
        from tickets.models import Ticket
        self.arrendatario.telefono = "+56912345678"
        self.arrendatario.save()
        ticket = Ticket.objects.create(
            condominio=self.condominio, usuario=self.arrendatario, unidad=self.unidad,
            titulo="Filtración", descripcion="Hay agua en el baño",
        )
        url = reverse("tickets:detalle", args=[ticket.pk])

        self.entrar(self.admin)
        r = self.client.get(url)
        self.assertContains(r, 'id="fichaUsuario"')
        self.assertContains(r, "Arrendatario")
        self.assertContains(r, "+56912345678")
        self.assertContains(r, "Historial de tickets")
        self.assertContains(r, "Arrendatario de la unidad")
        self.assertContains(r, reverse("usuario_editar", args=[self.arrendatario.id]))

        self.entrar(self.arrendatario)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, 'id="fichaUsuario"')


    # ---------- Quién ve las bajas ----------
    def test_bajas_solo_admin_y_gestor_del_condominio(self):
        User = get_user_model()
        ciudad = Ciudad.objects.get()
        otro_condo = Condominio.objects.create(nombre="Otro", direccion="X", ciudad=ciudad, edificios=1)

        def perfil(username, rol, condominio):
            user = User.objects.create_user(username=username, password="x")
            return Usuario.objects.create(user=user, condominio=condominio, nombre=username, apellido="P", rol=rol, email=f"{username}@x.cl")

        gestor = perfil("gestor", Usuario.Rol.GESTOR, self.condominio)
        superadmin = perfil("super", Usuario.Rol.SUPER, None)
        admin_ajeno = perfil("ajeno", Usuario.Rol.ADMINISTRADOR, otro_condo)

        with self.captureOnCommitCallbacks(execute=True):
            solicitud = self.solicitar()
        self.assertTrue(Notification.objects.filter(recipient=gestor.user).exists())
        self.assertFalse(Notification.objects.filter(recipient=superadmin.user).exists())
        self.assertFalse(Notification.objects.filter(recipient=admin_ajeno.user).exists())

        for p in (superadmin, admin_ajeno):
            self.entrar(p)
            r = self.client.get(reverse("solicitudes_baja_lista"))
            self.assertNotContains(r, "Terminó el contrato", status_code=r.status_code)
            self.client.post(reverse("solicitud_baja_resolver", args=[solicitud.id]), {"accion": "aprobar"})
            solicitud.refresh_from_db()
            self.assertEqual(solicitud.estado, "pendiente")
        self.entrar(superadmin)
        self.assertNotContains(self.client.get(reverse("inicio")), "Bajas de arrendatarios")

        self.entrar(gestor)
        self.assertContains(self.client.get(reverse("solicitudes_baja_lista")), "Terminó el contrato")
        self.client.post(reverse("solicitud_baja_resolver", args=[solicitud.id]), {"accion": "rechazar"})
        solicitud.refresh_from_db()
        self.assertEqual(solicitud.estado, "rechazada")


    def test_bandeja_sin_ticket_muestra_resumen(self):
        from tickets.models import Ticket
        Ticket.objects.create(condominio=self.condominio, usuario=self.arrendatario, unidad=self.unidad,
                              titulo="Ruido", descripcion="x")
        self.entrar(self.admin)
        r = self.client.get(reverse("tickets:lista"))
        self.assertContains(r, "Resumen de la bandeja")
        self.assertContains(r, "Sin asignar")
        self.assertContains(r, "Tickets y solicitudes | Comunidad al Día")
        self.entrar(self.arrendatario)
        self.assertContains(self.client.get(reverse("tickets:lista")), "Tus tickets")


    def test_visor_de_adjuntos_en_la_misma_pagina(self):
        from django.core.files.base import ContentFile
        from tickets.models import Ticket, TicketArchivo
        t = Ticket.objects.create(condominio=self.condominio, usuario=self.arrendatario, unidad=self.unidad,
                                  titulo="Foto", descripcion="x")
        a = TicketArchivo(ticket=t, nombre="foto.jpg")
        a.archivo.save("foto.jpg", ContentFile(b"\xff\xd8\xff"), save=True)
        self.entrar(self.arrendatario)
        r = self.client.get(reverse("tickets:detalle", args=[t.pk]))
        self.assertContains(r, 'data-visor="imagen"')
        self.assertContains(r, 'id="visor"')
        self.assertNotContains(r, 'target="_blank"')

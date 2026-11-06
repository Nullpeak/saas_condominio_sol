import shutil
import tempfile
from datetime import timedelta
from io import BytesIO

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import Ciudad, Condominio, Unidad, Usuario
from visitas.models import Visita
from notis.models import Notification
from .models import Ticket, TicketArchivo, TicketComentario, TicketEvento, TicketVisita

MEDIA_TMP = tempfile.mkdtemp()
Rol = Usuario.Rol
E = Ticket.Estado


def png(nombre="foto.png"):
    buf = BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class BaseTickets(TestCase):
    """Datos y ayudas comunes. No tiene tests propios."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TMP, ignore_errors=True)

    @classmethod
    def setUpTestData(cls):
        ciudad = Ciudad.objects.create(nombre="Santiago")
        cls.condo = Condominio.objects.create(nombre="Los Robles", direccion="x", ciudad=ciudad)
        cls.otro_condo = Condominio.objects.create(nombre="Otro", direccion="y", ciudad=ciudad)
        ed = cls.condo.edificios_set.first()
        ed.numero_pisos, ed.viviendas_por_piso = 2, 2
        ed.save()
        cls.unidad = ed.unidades.first()

        def perfil(username, rol, condo):
            u = get_user_model().objects.create_user(username, f"{username}@x.cl", "clave-segura-123")
            return Usuario.objects.create(user=u, nombre=username, apellido="T", email=f"{username}@x.cl",
                                          rol=rol, condominio=condo)

        cls.residente = perfil("resi", Rol.RESIDENTE, cls.condo)
        cls.vecino = perfil("vecino", Rol.RESIDENTE, cls.condo)
        cls.gestor = perfil("gestor", Rol.GESTOR, cls.condo)
        cls.gestor_otro = perfil("gestor2", Rol.GESTOR, cls.otro_condo)
        cls.superadmin = perfil("super", Rol.SUPER, cls.condo)
        cls.unidad.dueno = cls.residente
        cls.unidad.save()

    def entrar(self, perfil):
        self.client.force_login(perfil.user)

    def post(self, *args, **kwargs):
        # Las notificaciones se crean con on_commit: TestCase no hace commit, así que se ejecutan aquí
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(*args, **kwargs)

    def crear(self, **extra):
        self.entrar(self.residente)
        data = {"titulo": "Filtración", "categoria": "mantencion", "descripcion": "Gotea el techo",
                "unidad": self.unidad.pk, **extra}
        r = self.post(reverse("tickets:crear"), data)
        self.assertEqual(r.status_code, 302, getattr(r, "context", {}) and r.context["form"].errors)
        return Ticket.objects.latest("pk")


class TicketsTests(BaseTickets):

    # ------------------------------------------------------------ creación
    def test_residente_crea_ticket_con_foto_y_notifica_staff(self):
        t = self.crear(archivos=[png(), png("otra.png")])
        self.assertEqual(t.usuario, self.residente)
        self.assertEqual(t.condominio, self.condo)
        self.assertEqual(t.estado, E.ABIERTO)
        self.assertEqual(t.pruebas.count(), 2)
        self.assertTrue(TicketEvento.objects.filter(ticket=t, tipo="creado").exists())
        avisados = set(Notification.objects.values_list("recipient__username", flat=True))
        self.assertIn("gestor", avisados)
        self.assertIn("super", avisados)
        self.assertNotIn("gestor2", avisados)  # otro condominio
        self.assertTrue(Notification.objects.filter(url=reverse("tickets:detalle", args=[t.pk])).exists())

    def test_rechaza_archivo_que_no_es_imagen(self):
        self.entrar(self.residente)
        falso = SimpleUploadedFile("virus.png", b"no soy imagen", content_type="image/png")
        r = self.post(reverse("tickets:crear"), {
            "titulo": "x", "categoria": "otro", "descripcion": "x", "archivos": [falso]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Ticket.objects.exists())

    def test_residente_no_puede_elegir_unidad_ajena(self):
        ajena = Unidad.objects.exclude(pk=self.unidad.pk).first()
        self.entrar(self.residente)
        r = self.post(reverse("tickets:crear"), {
            "titulo": "x", "categoria": "otro", "descripcion": "x", "unidad": ajena.pk})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Ticket.objects.exists())

    # ------------------------------------------------------------ visibilidad
    def test_visibilidad(self):
        t = self.crear()
        url = reverse("tickets:detalle", args=[t.pk])
        for perfil, esperado in [(self.residente, 200), (self.gestor, 200), (self.superadmin, 200),
                                 (self.vecino, 404), (self.gestor_otro, 404)]:
            self.entrar(perfil)
            self.assertEqual(self.client.get(url).status_code, esperado, perfil)
        self.entrar(self.vecino)
        self.assertNotContains(self.client.get(reverse("tickets:lista")), "Filtración")

    def test_listas_cargan_para_todos_los_roles(self):
        self.crear()
        for perfil in (self.residente, self.gestor, self.superadmin):
            self.entrar(perfil)
            r = self.client.get(reverse("tickets:lista"), {"estado": "todos", "q": "#1"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(self.client.get(reverse("tickets:crear")).status_code, 200)

    def test_adjuntos_protegidos(self):
        t = self.crear(archivos=[png()])
        url = reverse("tickets:archivo", args=[t.pruebas.first().pk])
        self.entrar(self.residente)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.entrar(self.vecino)
        self.assertEqual(self.client.get(url).status_code, 404)

    # ------------------------------------------------------------ gestión
    def test_gestor_gestiona_y_queda_historial(self):
        t = self.crear()
        Notification.objects.all().delete()
        self.entrar(self.gestor)
        r = self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.EN_REVISION, "prioridad": "alta", "categoria": "mantencion", "asignado": self.gestor.pk})
        self.assertEqual(r.status_code, 302)
        t.refresh_from_db()
        self.assertEqual((t.estado, t.prioridad, t.asignado), (E.EN_REVISION, "alta", self.gestor))
        self.assertEqual(set(t.eventos.values_list("tipo", flat=True)), {"creado", "estado", "prioridad", "asignado"})
        # El residente recibe el cambio de estado; el gestor no se notifica a sí mismo
        self.assertTrue(Notification.objects.filter(recipient=self.residente.user).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.gestor.user).exists())

    def test_residente_no_puede_gestionar(self):
        t = self.crear()
        r = self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.RESUELTO, "categoria": "otro"})
        self.assertEqual(r.status_code, 403)

    def test_no_se_asigna_a_staff_de_otro_condominio(self):
        t = self.crear()
        self.entrar(self.gestor)
        r = self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.ABIERTO, "categoria": "otro", "asignado": self.gestor_otro.pk})
        self.assertEqual(r.status_code, 400)
        t.refresh_from_db()
        self.assertIsNone(t.asignado)

    def test_transicion_invalida_rechazada(self):
        t = self.crear()
        Ticket.objects.filter(pk=t.pk).update(estado=E.CERRADO)
        self.entrar(self.gestor)
        r = self.post(reverse("tickets:gestionar", args=[t.pk]), {"estado": E.RESUELTO, "categoria": "otro"})
        self.assertEqual(r.status_code, 400)

    # ------------------------------------------------------------ conversación y ciclo de vida
    def test_ciclo_completo(self):
        t = self.crear()
        self.entrar(self.gestor)
        self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.ESPERANDO, "categoria": "mantencion", "asignado": self.gestor.pk})
        self.post(reverse("tickets:comentar", args=[t.pk]), {"texto": "¿Fotos?"})
        self.post(reverse("tickets:comentar", args=[t.pk]), {"texto": "Llamar gasfíter", "interno": "on"})

        # El residente no ve la nota interna y al responder vuelve a revisión
        self.entrar(self.residente)
        r = self.client.get(reverse("tickets:detalle", args=[t.pk]))
        self.assertContains(r, "¿Fotos?")
        self.assertNotContains(r, "Llamar gasfíter")
        Notification.objects.all().delete()
        self.post(reverse("tickets:comentar", args=[t.pk]), {"texto": "Adjunto", "archivos": [png()]})
        t.refresh_from_db()
        self.assertEqual(t.estado, E.EN_REVISION)
        self.assertTrue(Notification.objects.filter(recipient=self.gestor.user).exists())

        # Se resuelve, el residente reabre, se resuelve de nuevo y confirma
        self.entrar(self.gestor)
        self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.RESUELTO, "categoria": "mantencion", "asignado": self.gestor.pk})
        t.refresh_from_db()
        self.assertIsNotNone(t.resuelto)

        self.entrar(self.residente)
        self.post(reverse("tickets:accion", args=[t.pk, "reabrir"]), {"motivo": "Sigue goteando"})
        t.refresh_from_db()
        self.assertEqual(t.estado, E.EN_REVISION)
        self.assertIsNone(t.resuelto)

        self.entrar(self.gestor)
        self.post(reverse("tickets:gestionar", args=[t.pk]), {
            "estado": E.RESUELTO, "categoria": "mantencion", "asignado": self.gestor.pk})
        self.entrar(self.residente)
        self.post(reverse("tickets:accion", args=[t.pk, "confirmar"]))
        t.refresh_from_db()
        self.assertEqual(t.estado, E.CERRADO)
        self.assertIsNotNone(t.cerrado)

        # Cerrado: el residente ya no puede comentar ni volver a confirmar
        self.assertEqual(self.post(reverse("tickets:comentar", args=[t.pk]), {"texto": "hola"}).status_code, 403)
        self.assertEqual(self.post(reverse("tickets:accion", args=[t.pk, "confirmar"])).status_code, 403)
        self.assertEqual(TicketComentario.objects.filter(ticket=t).count(), 4)

    def test_adjunto_de_nota_interna_oculto_al_residente(self):
        t = self.crear()
        self.entrar(self.gestor)
        self.post(reverse("tickets:comentar", args=[t.pk]),
                         {"texto": "presupuesto", "interno": "on", "archivos": [png()]})
        adj = TicketArchivo.objects.get(comentario__isnull=False)
        self.entrar(self.residente)
        self.assertEqual(self.client.get(reverse("tickets:archivo", args=[adj.pk])).status_code, 404)

    def test_residente_cancela_su_ticket(self):
        t = self.crear()
        self.post(reverse("tickets:accion", args=[t.pk, "cancelar"]))
        t.refresh_from_db()
        self.assertEqual(t.estado, E.CERRADO)


def fecha(dias, horas=0):
    return timezone.localtime(timezone.now() + timedelta(days=dias, hours=horas)).strftime("%Y-%m-%dT%H:%M")


def datos_visitas(*visitas):
    data = {"visitas-TOTAL_FORMS": str(len(visitas)), "visitas-INITIAL_FORMS": "0",
            "visitas-MIN_NUM_FORMS": "1", "visitas-MAX_NUM_FORMS": "10"}
    for i, v in enumerate(visitas):
        for k, val in v.items():
            data[f"visitas-{i}-{k}"] = val
    return data


VISITA_ANA = {"nombre": "Ana Pérez", "rut": "12.345.678-5", "patente": "ab-cd 12", "llegada": fecha(1), "salida": fecha(1, 4)}
VISITA_LUIS = {"nombre": "Luis Soto", "rut": "", "patente": "", "llegada": fecha(2), "salida": fecha(3)}


@override_settings(MEDIA_ROOT=MEDIA_TMP)
class VisitasTests(BaseTickets):

    def crear_visitas(self, *visitas, **extra):
        self.entrar(self.residente)
        data = {"titulo": "Visitas del fin de semana", "categoria": "visitas", "descripcion": "Vienen mis primos",
                "unidad": self.unidad.pk, **datos_visitas(*visitas), **extra}
        r = self.post(reverse("tickets:crear"), data)
        return r

    def revisar(self, ticket, visita_id, decision, **data):
        return self.post(reverse("tickets:revisar_visita", args=[ticket.pk, visita_id, decision]), data)

    def test_solicitud_queda_pendiente_sin_registrar(self):
        r = self.crear_visitas(VISITA_ANA, VISITA_LUIS)
        self.assertEqual(r.status_code, 302)
        t = Ticket.objects.get()
        self.assertEqual(t.categoria, "visitas")
        self.assertEqual(list(t.visitas.values_list("estado", flat=True)), ["pendiente", "pendiente"])
        self.assertEqual(t.visitas.first().patente, "ABCD12")
        self.assertFalse(Visita.objects.exists())
        self.assertContains(self.client.get(reverse("tickets:detalle", args=[t.pk])), "Ana Pérez")

    def test_validaciones(self):
        # sin unidad
        self.crear_visitas(VISITA_ANA, unidad="")
        # salida antes de llegada
        self.crear_visitas({**VISITA_ANA, "salida": fecha(0, 20)})
        self.crear_visitas({**VISITA_ANA, "llegada": fecha(-2), "salida": fecha(-1)})
        # RUT inválido
        self.crear_visitas({**VISITA_ANA, "rut": "12.345.678-0"})
        # sin ninguna visita
        self.crear_visitas()
        self.assertFalse(Ticket.objects.exists())

    def test_otra_categoria_ignora_visitas(self):
        self.crear_visitas({**VISITA_ANA, "nombre": ""}, categoria="mantencion")
        t = Ticket.objects.get()
        self.assertFalse(t.visitas.exists())

    def test_aprobar_registra_y_rechazar_resuelve(self):
        self.crear_visitas(VISITA_ANA, VISITA_LUIS)
        t = Ticket.objects.get()
        ana, luis = t.visitas.all()
        Notification.objects.all().delete()

        # El residente no puede aprobar
        self.assertEqual(self.revisar(t, ana.pk, "aprobar").status_code, 403)

        self.entrar(self.gestor)
        self.revisar(t, ana.pk, "aprobar")
        ana.refresh_from_db()
        self.assertEqual(ana.estado, "aprobada")
        v = ana.visita
        self.assertEqual((v.nombre, v.patente_vehiculo, v.unidad, v.condominio, v.estado, v.registrado_por),
                         ("Ana Pérez", "ABCD12", self.unidad, self.condo, Visita.Estado.PENDIENTE, self.gestor))
        self.assertEqual(v.fecha_visita, timezone.localtime(ana.llegada).date())
        t.refresh_from_db()
        self.assertTrue(t.esta_abierto)  # queda Luis pendiente

        # Aviso al residente
        self.assertTrue(Notification.objects.filter(recipient=self.residente.user, message__contains="aprobada").exists())

        # Aprobar dos veces no duplica
        self.revisar(t, ana.pk, "aprobar")
        self.assertEqual(Visita.objects.count(), 1)

        self.revisar(t, luis.pk, "rechazar", motivo="Sin estacionamiento ese día")
        luis.refresh_from_db()
        self.assertEqual((luis.estado, luis.motivo_rechazo), ("rechazada", "Sin estacionamiento ese día"))
        self.assertIsNone(luis.visita)
        t.refresh_from_db()
        self.assertEqual(t.estado, Ticket.Estado.RESUELTO)  # ya no quedan pendientes
        self.assertEqual(Visita.objects.count(), 1)

        # El residente ve la visita registrada en la app de visitas
        self.entrar(self.residente)
        self.assertContains(self.client.get(reverse("visitas:lista"), {"fecha": v.fecha_visita.isoformat()}), "Ana Pérez")

    def test_aprobar_todas(self):
        self.crear_visitas(VISITA_ANA, VISITA_LUIS)
        t = Ticket.objects.get()
        self.entrar(self.gestor)
        self.revisar(t, 0, "aprobar")
        self.assertEqual(Visita.objects.count(), 2)
        t.refresh_from_db()
        self.assertEqual(t.estado, Ticket.Estado.RESUELTO)

    def test_staff_de_otro_condominio_no_aprueba(self):
        self.crear_visitas(VISITA_ANA)
        t = Ticket.objects.get()
        self.entrar(self.gestor_otro)
        self.assertEqual(self.revisar(t, 0, "aprobar").status_code, 404)
        self.assertFalse(Visita.objects.exists())


class IntegracionGdcTests(BaseTickets):
    """Lo que se sumó al unir con feature/gdc-requisitos-funcionales."""

    def test_residente_edita_su_ticket_abierto(self):
        t = self.crear()
        url = reverse("tickets:editar", args=[t.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.post(url, {"titulo": "Filtración grave", "categoria": "convivencia", "descripcion": "Gotea mucho"})
        t.refresh_from_db()
        self.assertEqual((t.titulo, t.categoria), ("Filtración grave", "convivencia"))
        self.assertEqual(
            set(t.eventos.values_list("tipo", flat=True)), {"creado", "edicion", "categoria"}
        )

    def test_no_edita_si_ya_no_esta_abierto_o_no_es_suyo(self):
        t = self.crear()
        url = reverse("tickets:editar", args=[t.pk])
        self.entrar(self.gestor)  # el staff gestiona, no edita
        self.post(url, {"titulo": "Otro", "categoria": "otro", "descripcion": "x"})
        Ticket.objects.filter(pk=t.pk).update(estado=E.EN_REVISION)
        self.entrar(self.residente)
        self.post(url, {"titulo": "Otro", "categoria": "otro", "descripcion": "x"})
        t.refresh_from_db()
        self.assertEqual(t.titulo, "Filtración")

    def test_admin_con_varios_condominios(self):
        admin_u = get_user_model().objects.create_user("admin2", "admin2@x.cl", "clave-segura-123")
        admin = Usuario.objects.create(user=admin_u, nombre="Admin", apellido="Dos", email="admin2@x.cl",
                                       rol=Rol.ADMINISTRADOR, condominio=self.otro_condo)
        admin.condominios_administrados.add(self.condo)
        t = self.crear()
        # recibe el aviso, ve el ticket, puede gestionarlo y asignárselo
        self.assertTrue(Notification.objects.filter(recipient=admin_u, message__contains="Nuevo ticket").exists())
        self.entrar(admin)
        self.assertEqual(self.client.get(reverse("tickets:detalle", args=[t.pk])).status_code, 200)
        self.post(reverse("tickets:gestionar", args=[t.pk]),
                  {"estado": "en_revision", "prioridad": "alta", "categoria": "mantencion", "asignado": admin.pk})
        t.refresh_from_db()
        self.assertEqual((t.estado, t.asignado), (E.EN_REVISION, admin))

    def test_filtro_por_fecha_y_unidad(self):
        t = self.crear()
        self.entrar(self.gestor)
        hoy = timezone.localdate()
        url = reverse("tickets:lista")
        r = self.client.get(url, {"desde": hoy.isoformat(), "unidad": self.unidad.pk})
        self.assertEqual([x.pk for x in r.context["page"]], [t.pk])
        r = self.client.get(url, {"desde": (hoy + timedelta(days=1)).isoformat()})
        self.assertEqual(list(r.context["page"]), [])
        r = self.client.get(url, {"desde": "no-es-fecha"})  # se ignora
        self.assertEqual(r.status_code, 200)

    def test_adjunta_video(self):
        mp4 = SimpleUploadedFile("video.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, content_type="video/mp4")
        t = self.crear(archivos=[mp4])
        self.assertTrue(t.pruebas.get().es_video)
        falso = SimpleUploadedFile("falso.mp4", b"no soy un video" * 4, content_type="video/mp4")
        self.entrar(self.residente)
        r = self.post(reverse("tickets:crear"), {"titulo": "x", "categoria": "otro", "descripcion": "x",
                                                  "unidad": self.unidad.pk, "archivos": [falso]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 1)

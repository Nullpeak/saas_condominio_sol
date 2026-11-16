from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from anuncios.models import AnuncioOficial, AnuncioResidente
from core.models import (
    AreaComun, Condominio, Edificio, HorarioAreaComun, Unidad, Usuario,
    Reservacion,
)
from gastos_comunes.models import (
    DetalleCargo, ItemGasto, Liquidacion, PagoRegistrado, Periodo,
)
from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
from tickets.models import Ticket, TicketComentario, TicketEvento
from visitas.models import Visita


class Command(BaseCommand):
    help = "Agrega datos sintéticos DEMO a los condominios existentes, sin borrar registros."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="DemoGDC2026!", help="Contraseña común para cuentas demo locales")

    @transaction.atomic
    def handle(self, *args, **options):
        condominios = list(Condominio.objects.filter(activo=True).order_by("id"))
        if not condominios:
            raise CommandError("No hay condominios activos; no se creó ninguno.")
        User = get_user_model()
        password = options["password"]
        report = []
        today = timezone.localdate()

        for ci, condominio in enumerate(condominios, start=1):
            edificios = list(condominio.edificios_set.filter(activo=True).order_by("numero"))
            if not edificios:
                self.stdout.write(self.style.WARNING(f"{condominio.nombre}: sin edificios activos; se omite."))
                continue

            gestores = []
            for gi in (1, 2):
                email = f"demo.gestor.c{condominio.pk}.{gi}@example.invalid"
                auth, _ = User.objects.get_or_create(username=email, defaults={"email": email, "is_active": True})
                auth.email = email
                auth.set_password(password)
                auth.save()
                gestor, _ = Usuario.objects.get_or_create(
                    email=email,
                    defaults={"user": auth, "condominio": condominio, "nombre": "Gestor Demo", "apellido": f"C{condominio.pk}-{gi}", "rol": Usuario.Rol.GESTOR, "telefono": f"+5695555{condominio.pk:02d}{gi:02d}"},
                )
                gestor.user = auth
                gestor.condominio = condominio
                gestor.rol = Usuario.Rol.GESTOR
                gestor.activo = True
                gestor.save()
                gestor.condominios_administrados.add(condominio)
                gestores.append(gestor)

            unidades_activas = list(Unidad.objects.filter(edificio__condominio=condominio, edificio__activo=True, activo=True).select_related("edificio").order_by("edificio__numero", "piso", "posicion"))
            residentes = []
            for ri in (1, 2, 3):
                email = f"demo.residente.c{condominio.pk}.{ri}@example.invalid"
                auth, _ = User.objects.get_or_create(username=email, defaults={"email": email, "is_active": True})
                auth.email = email
                auth.set_password(password)
                auth.save()
                residente, _ = Usuario.objects.get_or_create(
                    email=email,
                    defaults={"user": auth, "condominio": condominio, "nombre": "Residente Demo", "apellido": f"C{condominio.pk}-{ri}", "rol": Usuario.Rol.RESIDENTE, "telefono": f"+5695666{condominio.pk:02d}{ri:02d}"},
                )
                residente.user = auth
                residente.condominio = condominio
                residente.rol = Usuario.Rol.RESIDENTE
                residente.activo = True
                residente.save()
                residentes.append(residente)

            # Vincula residentes demo solo a unidades vacías; no reemplaza dueños/arrendatarios existentes.
            libres = [u for u in unidades_activas if not u.dueno_id and not u.arrendatario_id]
            residentes_sin_unidad = [r for r in residentes if not Unidad.objects.filter(edificio__condominio=condominio, dueno=r).exists() and not Unidad.objects.filter(edificio__condominio=condominio, arrendatario=r).exists()]
            for residente, unidad in zip(residentes_sin_unidad, libres):
                unidad.dueno = residente
                unidad.save(update_fields=["dueno"])
            unidades_demo = [u for u in unidades_activas if u.dueno_id in {r.pk for r in residentes} or u.arrendatario_id in {r.pk for r in residentes}]

            # Si el condominio no tenía unidades activas, agrega dos unidades demo sin tocar las históricas.
            if not unidades_activas:
                edificio = edificios[0]
                shares_demo = (Decimal("33.333333"), Decimal("33.333333"), Decimal("33.333334"))
                for ri, residente in enumerate(residentes, start=1):
                    numero = f"D{condominio.pk}{ri:02d}"
                    unidad, _ = Unidad.objects.get_or_create(edificio=edificio, numero_unidad=numero, defaults={"piso": 90 + ri, "posicion": ri, "dueno": residente, "alicuota": shares_demo[ri - 1], "cantidad_residentes": 2})
                    unidades_demo.append(unidad)
            elif len(unidades_demo) < 2 and len(unidades_activas) < 144:
                edificio = edificios[0]
                for ri in range(1, 3 - len(unidades_demo)):
                    residente = residentes[(len(unidades_demo) + ri - 1) % len(residentes)]
                    numero = f"D{condominio.pk}{ri:02d}"
                    unidad, _ = Unidad.objects.get_or_create(edificio=edificio, numero_unidad=numero, defaults={"piso": 90 + ri, "posicion": ri, "dueno": residente, "alicuota": Decimal("0"), "cantidad_residentes": 2})
                    unidades_demo.append(unidad)
            unidades_demo = list({u.pk: u for u in unidades_demo}.values())
            if not unidades_demo:
                unidades_demo = unidades_activas[:3]

            # Áreas y horarios
            areas = []
            for nombre, descripcion, capacidad in (
                ("DEMO - Quincho", "Espacio de prueba para reuniones familiares.", 20),
                ("DEMO - Sala multiuso", "Sala de actividades y reuniones de residentes.", 30),
            ):
                area, _ = AreaComun.objects.get_or_create(condominio=condominio, nombre=nombre, defaults={"descripcion": descripcion, "capacidad": capacidad, "reservable": True, "requiere_gastos_al_dia": False, "duracion_maxima": 4, "requiere_reserva": True, "permite_reservas_consecutivas": False, "dias_anticipacion_maxima": 60})
                areas.append(area)
                if not area.horarios.exists():
                    HorarioAreaComun.objects.bulk_create([HorarioAreaComun(area_comun=area, dia_semana=d, hora_inicio=time(9, 0), hora_fin=time(22, 0)) for d in range(7)])

            # Anuncios oficiales y anuncios enviados por residentes con variedad de estados.
            for ai, (estado, titulo) in enumerate(((AnuncioOficial.Estado.PUBLICADO, "DEMO - Mantención programada"), (AnuncioOficial.Estado.BORRADOR, "DEMO - Borrador de comunicado"), (AnuncioOficial.Estado.ARCHIVADO, "DEMO - Aviso archivado"))):
                AnuncioOficial.objects.get_or_create(condominio=condominio, titulo=titulo, defaults={"autor": gestores[0], "descripcion": "Contenido ficticio para visualizar el módulo de anuncios.", "estado": estado, "alcance": AnuncioOficial.Alcance.TODOS})
            for ri, estado in enumerate((AnuncioResidente.Estado.PENDIENTE, AnuncioResidente.Estado.PUBLICADO, AnuncioResidente.Estado.RECHAZADO)):
                if not unidades_demo:
                    break
                titulo = f"DEMO - Anuncio residente {ri + 1}"
                obj, created = AnuncioResidente.objects.get_or_create(condominio=condominio, titulo=titulo, defaults={"autor": residentes[ri % len(residentes)], "unidad": unidades_demo[ri % len(unidades_demo)], "descripcion": "Publicación ficticia para revisar aprobación y moderación.", "estado": estado, "moderado_por": gestores[0] if estado != AnuncioResidente.Estado.PENDIENTE else None, "moderado": timezone.now() if estado != AnuncioResidente.Estado.PENDIENTE else None})

            # Tickets con estados y respuestas/historial de muestra.
            estados_ticket = (Ticket.Estado.ABIERTO, Ticket.Estado.EN_REVISION, Ticket.Estado.ESPERANDO, Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO)
            for ti, estado in enumerate(estados_ticket):
                residente = residentes[ti % len(residentes)]
                unidad = unidades_demo[ti % len(unidades_demo)] if unidades_demo else None
                titulo = f"DEMO - Solicitud de prueba {ti + 1}"
                ticket, created = Ticket.objects.get_or_create(condominio=condominio, titulo=titulo, defaults={"usuario": residente, "unidad": unidad, "categoria": Ticket.Categoria.MANTENCION if ti % 2 else Ticket.Categoria.AREAS_COMUNES, "descripcion": "Registro ficticio para visualizar el ciclo de vida del ticket.", "estado": estado, "prioridad": Ticket.Prioridad.MEDIA, "asignado": gestores[0], "resuelto": timezone.now() if estado in (Ticket.Estado.RESUELTO, Ticket.Estado.CERRADO) else None, "cerrado": timezone.now() if estado == Ticket.Estado.CERRADO else None})
                if created:
                    TicketEvento.objects.get_or_create(ticket=ticket, tipo=TicketEvento.Tipo.CREADO, defaults={"autor": residente, "nuevo": "Ticket DEMO creado"})
                TicketComentario.objects.get_or_create(ticket=ticket, autor=gestores[0], texto="DEMO - Estamos revisando esta solicitud.", defaults={"interno": False})

            # Reservas pasadas y futuras, cubriendo aprobada, pendiente, completada y cancelada.
            if areas and unidades_demo:
                reserv_specs = ((-14, Reservacion.Estado.COMPLETADO), (5, Reservacion.Estado.APROBADO), (10, Reservacion.Estado.PENDIENTE), (18, Reservacion.Estado.CANCELADO))
                for rix, (delta, estado) in enumerate(reserv_specs):
                    starts = timezone.make_aware(datetime.combine(today + timedelta(days=delta), time(18, 0)))
                    ends = starts + timedelta(hours=2)
                    Reservacion.objects.get_or_create(unidad=unidades_demo[rix % len(unidades_demo)], area_comun=areas[rix % len(areas)], fecha_reserva=starts, defaults={"tipo_reserva": Reservacion.Tipo.AREA_COMUN, "usuario": residentes[rix % len(residentes)], "fin_reserva": ends, "estado": estado, "notas": "DEMO - Reserva sintética."})

            # Visitas de ejemplo en distintos estados.
            for vi, estado in enumerate((Visita.Estado.PENDIENTE, Visita.Estado.PRESENTE, Visita.Estado.FINALIZADA, Visita.Estado.CANCELADA)):
                if unidades_demo:
                    Visita.objects.get_or_create(condominio=condominio, nombre=f"DEMO - Visita {vi + 1} C{condominio.pk}", defaults={"edificio": unidades_demo[vi % len(unidades_demo)].edificio, "unidad": unidades_demo[vi % len(unidades_demo)], "registrado_por": residentes[vi % len(residentes)], "fecha_visita": today + timedelta(days=vi - 1), "motivo": "Visita de demostración", "estado": estado})

            # Mantenciones con estado e historial por condominio.
            for mi, nombre in enumerate(("DEMO - Ascensor", "DEMO - Bombas de agua", "DEMO - Portón de acceso")):
                elemento, _ = ElementoMantenimiento.objects.get_or_create(condominio=condominio, nombre=nombre, defaults={"categoria": "Equipo", "ubicacion": "Sector común", "empresa_actual": "Proveedor de demostración", "creado_por": gestores[0]})
                estado = (RegistroMantenimiento.Estado.PROGRAMADO, RegistroMantenimiento.Estado.EN_CURSO, RegistroMantenimiento.Estado.COMPLETADO)[mi]
                RegistroMantenimiento.objects.get_or_create(elemento=elemento, fecha=today + timedelta(days=mi - 1), descripcion="DEMO - Inspección preventiva sintética.", defaults={"tipo_mantenimiento": RegistroMantenimiento.TipoMantenimiento.PREVENTIVO, "estado": estado, "detalle_cierre": "Trabajo de ejemplo completado." if estado == RegistroMantenimiento.Estado.COMPLETADO else "", "empresa": "Servicio Técnico DEMO", "creado_por": gestores[0]})

            # Períodos históricos de gastos: genera liquidaciones de prueba y pagos contables manuales DEMO.
            units_fin = list(Unidad.objects.filter(edificio__condominio=condominio, edificio__activo=True, activo=True, alicuota__isnull=False).select_related("edificio").order_by("edificio__numero", "piso", "posicion"))
            shares = sum((u.alicuota for u in units_fin), Decimal("0"))
            if units_fin and shares == Decimal("100"):
                for offset, month in enumerate((8, 9, 10)):
                    year = 2026
                    periodo, _ = Periodo.objects.get_or_create(condominio=condominio, anio=year, mes=month, defaults={"vencimiento": date(year, month, 15), "recargo_reserva": Decimal("5.00"), "estado": Periodo.Estado.PUBLICADO, "creado_por": gestores[0], "publicado": timezone.make_aware(datetime(year, month, 1, 9, 0))})
                    if periodo.estado != Periodo.Estado.PUBLICADO:
                        periodo.estado = Periodo.Estado.PUBLICADO
                        periodo.publicado = timezone.make_aware(datetime(year, month, 1, 9, 0))
                        periodo.save(update_fields=["estado", "publicado"])
                    if not periodo.gastos.exists():
                        gasto = ItemGasto.objects.create(periodo=periodo, concepto="DEMO - Gastos operacionales", tipo=ItemGasto.Tipo.ORDINARIO, alcance=ItemGasto.Alcance.CONDOMINIO, monto=Decimal("7200000"))
                    else:
                        gasto = periodo.gastos.first()
                    per_unit = (Decimal("7200000") * Decimal("0.95") / Decimal("100"))
                    for ui, unidad in enumerate(units_fin):
                        base = (per_unit * unidad.alicuota).quantize(Decimal("1"))
                        reserva = (base * Decimal("0.05")).quantize(Decimal("1"))
                        total = base + reserva
                        liq, created = Liquidacion.objects.get_or_create(periodo=periodo, unidad=unidad, defaults={"alicuota_usada": unidad.alicuota, "total_ordinario": base, "fondo_reserva": reserva, "total": total})
                        if created:
                            DetalleCargo.objects.create(liquidacion=liq, concepto="DEMO - Gastos operacionales", categoria="ordinario", monto=base, item_gasto=gasto)
                            DetalleCargo.objects.create(liquidacion=liq, concepto="DEMO - Fondo de reserva (5%)", categoria="reserva", monto=reserva)
                        if not liq.pagos.filter(referencia__startswith="DEMO-").exists():
                            if ui % 4 == 0:
                                paid = liq.total
                            elif ui % 4 == 1:
                                paid = (liq.total * Decimal("0.05")).quantize(Decimal("1"))
                            elif ui % 4 == 2:
                                paid = (liq.total * Decimal("0.55")).quantize(Decimal("1"))
                            else:
                                paid = Decimal("0")
                            if paid > 0:
                                PagoRegistrado.objects.create(liquidacion=liq, monto=paid, fecha=min(today, date(year, month, 28)), metodo="Transferencia DEMO", referencia=f"DEMO-C{condominio.pk}-{year}{month:02d}-U{unidad.pk}", registrado_por=gestores[0])
                report.append((condominio.nombre, len(residentes), len(unidades_demo), len(units_fin)))
            else:
                report.append((condominio.nombre, len(residentes), len(unidades_demo), 0))

        self.stdout.write(self.style.SUCCESS("Datos DEMO agregados sin eliminar registros:"))
        for row in report:
            self.stdout.write(f"- {row[0]}: residentes demo={row[1]}, unidades demo asociadas={row[2]}, unidades con liquidaciones={row[3]}" )
        self.stdout.write(f"Contraseña local común de cuentas demo: {password}")
        self.stdout.write("Identifica estos registros por el prefijo DEMO y correos @example.invalid. No hay pagos de Mercado Pago." )

import csv
from datetime import date
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
from .models import AreaComun, Reservacion, Usuario


ROLES_REPORTES = (Usuario.Rol.SUPER, Usuario.Rol.ADMINISTRADOR)
TIPOS_REPORTE = ("reservas", "mantenimiento")


def _fecha_filtro(valor, etiqueta, errores):
    if not valor:
        return None
    try:
        return date.fromisoformat(valor)
    except ValueError:
        errores.append(f"La fecha {etiqueta} no es válida.")
        return None


def _csv_response(nombre, encabezados, filas):
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    respuesta.write("\ufeff")
    escritor = csv.writer(respuesta)
    escritor.writerow(encabezados)
    escritor.writerows(filas)
    return respuesta


@login_required
def reportes(request):
    usuario = request.user.usuario
    if usuario.rol not in ROLES_REPORTES:
        return redirect("inicio")

    condominios = usuario.condominios_asignados().filter(activo=True).order_by("nombre")
    if not condominios.exists():
        return redirect("inicio")

    errores = []
    tipo = request.GET.get("tipo", "reservas")
    if tipo not in TIPOS_REPORTE:
        tipo = "reservas"
    desde_texto = request.GET.get("desde", "").strip()
    hasta_texto = request.GET.get("hasta", "").strip()
    desde = _fecha_filtro(desde_texto, "desde", errores)
    hasta = _fecha_filtro(hasta_texto, "hasta", errores)
    if desde and hasta and desde > hasta:
        errores.append("La fecha inicial no puede ser posterior a la fecha final.")

    condominio_id = request.GET.get("condominio", "").strip()
    condominio_seleccionado = None
    if condominio_id:
        condominio_seleccionado = condominios.filter(pk=condominio_id).first()
        if not condominio_seleccionado:
            errores.append("No tienes acceso al condominio seleccionado.")
            condominio_id = ""
    condominios_filtro = (
        condominios.filter(pk=condominio_seleccionado.pk)
        if condominio_seleccionado
        else condominios
    )

    exportar = request.GET.get("exportar") == "csv"
    filtros = request.GET.copy()
    filtros["exportar"] = "csv"
    export_url = f"?{urlencode(filtros, doseq=True)}"
    contexto = {
        "usuario_actual": usuario,
        "condominios": condominios,
        "condominio_filtro": condominio_id,
        "tipo": tipo,
        "desde": desde_texto,
        "hasta": hasta_texto,
        "errores": errores,
        "export_url": export_url,
    }

    if tipo == "reservas":
        areas = AreaComun.objects.filter(condominio__in=condominios_filtro).order_by(
            "condominio__nombre", "nombre"
        )
        area_id = request.GET.get("area", "").strip()
        area = areas.filter(pk=area_id).first() if area_id else None
        if area_id and not area:
            errores.append("El área seleccionada no pertenece a los condominios disponibles.")
            area_id = ""
        reservas = Reservacion.objects.filter(
            tipo_reserva=Reservacion.Tipo.AREA_COMUN,
            unidad__edificio__condominio__in=condominios_filtro,
        ).select_related(
            "area_comun", "unidad", "unidad__edificio", "unidad__edificio__condominio", "usuario"
        )
        if area:
            reservas = reservas.filter(area_comun=area)
        if desde:
            reservas = reservas.filter(fecha_reserva__date__gte=desde)
        if hasta:
            reservas = reservas.filter(fecha_reserva__date__lte=hasta)

        resumen = list(reservas.values("area_comun__nombre").annotate(
            total=Count("id"),
            pendientes=Count("id", filter=Q(estado=Reservacion.Estado.PENDIENTE)),
            aprobadas=Count("id", filter=Q(estado=Reservacion.Estado.APROBADO)),
            completadas=Count("id", filter=Q(estado=Reservacion.Estado.COMPLETADO)),
            canceladas=Count("id", filter=Q(estado=Reservacion.Estado.CANCELADO)),
            rechazadas=Count("id", filter=Q(estado=Reservacion.Estado.RECHAZADO)),
        ).order_by("area_comun__nombre"))
        if exportar and not errores:
            filas = (
                (
                    reserva.unidad.edificio.condominio.nombre,
                    reserva.area_comun.nombre if reserva.area_comun else "",
                    reserva.unidad.edificio.nombre,
                    reserva.unidad.numero_unidad,
                    timezone.localtime(reserva.fecha_reserva).strftime("%Y-%m-%d %H:%M"),
                    timezone.localtime(reserva.fin_reserva).strftime("%Y-%m-%d %H:%M") if reserva.fin_reserva else "",
                    reserva.get_estado_display(),
                    f"{reserva.usuario.nombre} {reserva.usuario.apellido}".strip(),
                )
                for reserva in reservas.order_by("fecha_reserva", "id").iterator()
            )
            return _csv_response(
                "reservas_condominio.csv",
                ("Condominio", "Área", "Edificio", "Unidad", "Inicio", "Término", "Estado", "Residente"),
                filas,
            )
        contexto.update({
            "areas": areas,
            "area_filtro": area_id,
            "total": reservas.count(),
            "resumen": resumen,
            "registros": reservas.order_by("-fecha_reserva", "-id")[:100],
            "tipo_nombre": "Estadísticas de reservas",
        })
    else:
        elementos = ElementoMantenimiento.objects.filter(condominio__in=condominios_filtro)
        categorias = list(elementos.order_by("categoria").values_list("categoria", flat=True).distinct())
        categoria = request.GET.get("categoria", "").strip()
        if categoria and categoria in categorias:
            elementos = elementos.filter(categoria=categoria)
        elif categoria:
            errores.append("La categoría seleccionada no pertenece a los condominios disponibles.")
            categoria = ""
        registros = RegistroMantenimiento.objects.filter(elemento__in=elementos).select_related(
            "elemento", "elemento__condominio", "creado_por"
        )
        if desde:
            registros = registros.filter(fecha__gte=desde)
        if hasta:
            registros = registros.filter(fecha__lte=hasta)

        resumen = list(registros.values("elemento__categoria").annotate(
            total=Count("id"),
            elementos=Count("elemento", distinct=True),
            proximos=Count("id", filter=Q(proximo_mantenimiento__gte=timezone.localdate())),
            vencidos=Count("id", filter=Q(proximo_mantenimiento__lt=timezone.localdate())),
        ).order_by("elemento__categoria"))
        if exportar and not errores:
            filas = (
                (
                    registro.elemento.condominio.nombre,
                    registro.elemento.categoria,
                    registro.elemento.nombre,
                    registro.fecha.isoformat(),
                    registro.descripcion,
                    registro.empresa,
                    registro.proximo_mantenimiento.isoformat() if registro.proximo_mantenimiento else "",
                )
                for registro in registros.order_by("-fecha", "-id").iterator()
            )
            return _csv_response(
                "mantenimiento_condominio.csv",
                ("Condominio", "Categoría", "Elemento", "Fecha", "Descripción", "Empresa", "Próximo mantenimiento"),
                filas,
            )
        contexto.update({
            "categorias": categorias,
            "categoria_filtro": categoria,
            "total": registros.count(),
            "resumen": resumen,
            "registros": registros.order_by("-fecha", "-id")[:100],
            "tipo_nombre": "Historial de mantenimiento",
            "mantenimientos_vencidos": registros.filter(
                proximo_mantenimiento__lt=timezone.localdate()
            ).count(),
        })

    return render(request, "core/reportes.html", contexto)

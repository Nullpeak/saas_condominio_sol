import csv
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from mantenimientos.models import ElementoMantenimiento, RegistroMantenimiento
from tickets.models import Ticket
from gastos_comunes.models import DetalleCargo, Periodo
from gastos_comunes.services import resumen_mora, saldos_acumulados
from .models import AreaComun, Condominio, Reservacion, Unidad, Usuario


ROLES_REPORTES = (Usuario.Rol.ADMINISTRADOR,)
TIPOS_REPORTE = ("reservas", "mantenimiento", "tickets", "morosidad", "gastos")


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


def _columna_excel(numero):
    resultado = ""
    while numero:
        numero, resto = divmod(numero - 1, 26)
        resultado = chr(65 + resto) + resultado
    return resultado


def _celda_excel(valor, referencia, estilo=0):
    estilo_xml = f' s="{estilo}"' if estilo else ""
    if valor is None:
        return f'<c r="{referencia}"{estilo_xml}/>'
    if isinstance(valor, bool):
        return f'<c r="{referencia}" t="b"{estilo_xml}><v>{int(valor)}</v></c>'
    if isinstance(valor, (int, float, Decimal)):
        numero = str(valor)
        return f'<c r="{referencia}"{estilo_xml}><v>{escape(numero)}</v></c>'
    texto = escape(str(valor))
    return f'<c r="{referencia}" t="inlineStr"{estilo_xml}><is><t xml:space="preserve">{texto}</t></is></c>'


def _es_monetario(etiqueta):
    return any(
        palabra in str(etiqueta).lower()
        for palabra in ("monto", "saldo", "total", "interés", "principal", "pagado", "gasto", "clp")
    )


def _hoja_excel(titulo, subtitulo, encabezados, filas, resumen=None, ancho_min=12, nota=""):
    # Resumen y detalle quedan en la misma hoja, sin referencias a partes XLSX inexistentes.
    resumen = list(resumen or [])
    filas_detalle = [list(fila) for fila in filas]
    columnas = max(len(encabezados), 2)
    ultima_columna = _columna_excel(columnas)
    celdas_por_fila = {}
    combinaciones = []
    anchos = [ancho_min] * columnas

    def celda(fila, columna, valor, estilo=0):
        celdas_por_fila.setdefault(fila, []).append(
            (columna, _celda_excel(valor, f"{_columna_excel(columna)}{fila}", estilo))
        )
        if columna <= len(anchos):
            ancho_valor = len(str(valor or "")) + 2
            if estilo in (5, 8) and isinstance(valor, (int, float, Decimal)):
                # Excel also needs room for the currency symbol and thousands separators.
                digitos = len(str(valor).lstrip("-"))
                ancho_valor = max(36, digitos + digitos // 3 + 8)
            anchos[columna - 1] = max(anchos[columna - 1], min(80, ancho_valor))

    def fila(numero, valores, estilos=None):
        estilos = estilos or {}
        for columna, valor in enumerate(valores, start=1):
            celda(numero, columna, valor, estilos.get(columna, 0))

    def combinar(numero, inicio, fin, valor, estilo):
        celda(numero, inicio, valor, estilo)
        combinaciones.append(f"{_columna_excel(inicio)}{numero}:{_columna_excel(fin)}{numero}")

    combinar(1, 1, columnas, titulo, 1)
    combinar(2, 1, columnas, subtitulo, 2)
    if nota:
        combinar(3, 1, columnas, nota, 2)

    combinar(5, 1, columnas, "RESUMEN EJECUTIVO", 9)
    fila(6, ["Indicador", "Resultado"], {1: 3, 2: 3})
    for numero, (etiqueta, valor) in enumerate(resumen, start=7):
        estilo_valor = 8 if _es_monetario(etiqueta) else 7 if isinstance(valor, (int, float, Decimal)) else 0
        fila(numero, [etiqueta, valor], {1: 6, 2: estilo_valor})

    fila_seccion = 8 + len(resumen)
    combinar(fila_seccion, 1, columnas, "DETALLE", 9)
    fila_encabezados = fila_seccion + 1
    fila(fila_encabezados, encabezados, {indice: 3 for indice in range(1, len(encabezados) + 1)})

    columnas_monetarias = [
        indice for indice, encabezado in enumerate(encabezados, start=1)
        if _es_monetario(encabezado)
    ]
    for numero, datos in enumerate(filas_detalle, start=fila_encabezados + 1):
        estilos = {
            indice: (5 if _es_monetario(encabezados[indice - 1]) else 4)
            for indice, valor in enumerate(datos, start=1)
            if isinstance(valor, (int, float, Decimal)) and indice <= len(encabezados)
        }
        fila(numero, datos, estilos)

    fila_totales = fila_encabezados + len(filas_detalle) + 1
    if filas_detalle and columnas_monetarias:
        totales = [""] * len(encabezados)
        totales[0] = "TOTALES"
        for indice in columnas_monetarias:
            totales[indice - 1] = sum(
                (Decimal(datos[indice - 1] or 0) for datos in filas_detalle), Decimal("0")
            )
        fila(fila_totales, totales, {1: 3, **{indice: 5 for indice in columnas_monetarias}})
        ultima_fila_datos = fila_totales - 1
    else:
        ultima_fila_datos = fila_encabezados + len(filas_detalle)

    contenido_filas = "".join(
        f'<row r="{numero}">{"".join(xml for _, xml in sorted(celdas))}</row>'
        for numero, celdas in sorted(celdas_por_fila.items())
    )
    columnas_xml = "".join(
        f'<col min="{indice}" max="{indice}" width="{ancho}" customWidth="1"/>'
        for indice, ancho in enumerate(anchos, start=1)
    )
    merges = "".join(f'<mergeCell ref="{rango}"/>' for rango in combinaciones)
    filtro = f"A{fila_encabezados}:{_columna_excel(len(encabezados))}{max(fila_encabezados, ultima_fila_datos)}"
    ultima_fila = max(fila_seccion, fila_totales if filas_detalle and columnas_monetarias else ultima_fila_datos)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="A1:{ultima_columna}{ultima_fila}"/>'
        f'<sheetViews><sheetView workbookViewId="0"><pane ySplit="{fila_encabezados}" topLeftCell="A{fila_encabezados + 1}" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f'<cols>{columnas_xml}</cols><sheetData>{contenido_filas}</sheetData>'
        f'<autoFilter ref="{filtro}"/>'
        f'<mergeCells count="{len(combinaciones)}">{merges}</mergeCells>'
        '</worksheet>'
    )


def _xlsx_response(nombre, titulo, subtitulo, encabezados, filas, resumen, nota=""):
    hoja_xml = _hoja_excel(titulo, subtitulo, encabezados, filas, resumen, nota=nota)
    contenido = BytesIO()
    with zipfile.ZipFile(contenido, "w", zipfile.ZIP_DEFLATED) as libro:
        libro.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>''')
        libro.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>''')
        libro.writestr("xl/workbook.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Informe" sheetId="1" r:id="rId1"/></sheets></workbook>''')
        libro.writestr("xl/_rels/workbook.xml.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        libro.writestr("xl/styles.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="&quot;$&quot; #,##0;[Red]-&quot;$&quot; #,##0"/></numFmts><fonts count="5"><font><sz val="10"/><name val="Aptos"/></font><font><b/><sz val="18"/><color rgb="FF183153"/><name val="Aptos Display"/></font><font><i/><sz val="10"/><color rgb="FF64748B"/><name val="Aptos"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Aptos"/></font><font><b/><sz val="9"/><color rgb="FF36536E"/><name val="Aptos"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF183153"/><bgColor indexed="64"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEAF1F8"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="3"><border/><border><bottom style="thin"><color rgb="FFD7E0EA"/></bottom></border><border><left style="thin"><color rgb="FFD7E0EA"/></left><right style="thin"><color rgb="FFD7E0EA"/></right><top style="thin"><color rgb="FFD7E0EA"/></top><bottom style="thin"><color rgb="FFD7E0EA"/></bottom></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="10"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFill="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="4" fillId="3" borderId="2" xfId="0" applyFill="1"/><xf numFmtId="3" fontId="1" fillId="3" borderId="2" xfId="0" applyFill="1" applyNumberFormat="1"/><xf numFmtId="164" fontId="1" fillId="3" borderId="2" xfId="0" applyFill="1" applyNumberFormat="1"/><xf numFmtId="0" fontId="3" fillId="2" borderId="1" xfId="0" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''')
        libro.writestr("xl/worksheets/sheet1.xml", hoja_xml)
    respuesta = HttpResponse(
        contenido.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    respuesta["Content-Disposition"] = f'attachment; filename="{nombre}"'
    return respuesta

def _entregar_exportacion(exportar, nombre, titulo, subtitulo, encabezados, filas, resumen, nota=""):
    if exportar == "xlsx":
        return _xlsx_response(nombre.replace(".csv", ".xlsx"), titulo, subtitulo, encabezados, filas, resumen, nota)
    if exportar == "csv":
        return _csv_response(nombre, encabezados, filas)
    return None


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
    condominio_seleccionado = condominios.filter(pk=condominio_id).first() if condominio_id else None
    if condominio_id and not condominio_seleccionado:
        errores.append("No tienes acceso al condominio seleccionado.")
        condominio_id = ""
    condominios_filtro = condominios.filter(pk=condominio_seleccionado.pk) if condominio_seleccionado else condominios
    exportar = request.GET.get("exportar", "")
    filtros = request.GET.copy()
    filtros["exportar"] = "xlsx"
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
        "notas_reporte": [],
    }
    subtitulo = f"Generado el {timezone.localtime():%d/%m/%Y %H:%M} · Condominio: {condominio_seleccionado.nombre if condominio_seleccionado else 'Todos los disponibles'}"
    if desde or hasta:
        subtitulo += f" · Fechas: {desde.strftime('%d/%m/%Y') if desde else 'inicio abierto'} a {hasta.strftime('%d/%m/%Y') if hasta else 'actualidad'}"

    if tipo == "reservas":
        areas = AreaComun.objects.filter(condominio__in=condominios_filtro).order_by("condominio__nombre", "nombre")
        area_id = request.GET.get("area", "").strip()
        area = areas.filter(pk=area_id).first() if area_id else None
        if area_id and not area:
            errores.append("El área seleccionada no pertenece a los condominios disponibles.")
            area_id = ""
        reservas = Reservacion.objects.filter(
            tipo_reserva=Reservacion.Tipo.AREA_COMUN,
            unidad__edificio__condominio__in=condominios_filtro,
        ).select_related("area_comun", "unidad", "unidad__edificio", "unidad__edificio__condominio", "usuario")
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
        encabezados = ("Condominio", "Área común", "Edificio", "Unidad", "Inicio", "Término", "Estado", "Residente")
        filas = [(
            r.unidad.edificio.condominio.nombre, r.area_comun.nombre if r.area_comun else "",
            r.unidad.edificio.nombre, r.unidad.numero_unidad,
            timezone.localtime(r.fecha_reserva).strftime("%d/%m/%Y %H:%M"),
            timezone.localtime(r.fin_reserva).strftime("%d/%m/%Y %H:%M") if r.fin_reserva else "",
            r.get_estado_display(), f"{r.usuario.nombre} {r.usuario.apellido}".strip(),
        ) for r in reservas.order_by("fecha_reserva", "id")]
        titulo = "Informe de reservas de áreas comunes"
        resumen_xlsx = [("Reservas registradas", reservas.count()),
                        ("Pendientes", reservas.filter(estado=Reservacion.Estado.PENDIENTE).count()),
                        ("Aprobadas", reservas.filter(estado=Reservacion.Estado.APROBADO).count()),
                        ("Completadas", reservas.filter(estado=Reservacion.Estado.COMPLETADO).count()),
                        ("Canceladas", reservas.filter(estado=Reservacion.Estado.CANCELADO).count()),
                        ("Rechazadas", reservas.filter(estado=Reservacion.Estado.RECHAZADO).count())]
        if exportar and not errores:
            salida = _entregar_exportacion(exportar, "reservas_condominio.csv", titulo, subtitulo, encabezados, filas, resumen_xlsx)
            if salida:
                return salida
        contexto.update({"areas": areas, "area_filtro": area_id, "total": reservas.count(), "resumen": resumen,
                         "registros": reservas.order_by("-fecha_reserva", "-id")[:100], "tipo_nombre": "Reservas de áreas comunes",
                         "columnas_resumen": ("Área común", "Reservas", "Pendientes", "Aprobadas", "Completadas", "Canceladas", "Rechazadas")})

    elif tipo == "mantenimiento":
        elementos = ElementoMantenimiento.objects.filter(condominio__in=condominios_filtro)
        categorias = list(elementos.order_by("categoria").values_list("categoria", flat=True).distinct())
        categoria = request.GET.get("categoria", "").strip()
        if categoria and categoria in categorias:
            elementos = elementos.filter(categoria=categoria)
        elif categoria:
            errores.append("La categoría seleccionada no pertenece a los condominios disponibles.")
            categoria = ""
        registros = RegistroMantenimiento.objects.filter(elemento__in=elementos).select_related("elemento", "elemento__condominio", "creado_por")
        if desde:
            registros = registros.filter(fecha__gte=desde)
        if hasta:
            registros = registros.filter(fecha__lte=hasta)
        resumen = list(registros.values("elemento__categoria", "tipo_mantenimiento").annotate(
            total=Count("id"), elementos=Count("elemento", distinct=True),
            proximos=Count("id", filter=Q(proximo_mantenimiento__gte=timezone.localdate())),
            vencidos=Count("id", filter=Q(proximo_mantenimiento__lt=timezone.localdate())),
        ).order_by("elemento__categoria"))
        etiquetas_tipo = dict(RegistroMantenimiento.TipoMantenimiento.choices)
        for fila in resumen:
            fila["tipo_mantenimiento_display"] = etiquetas_tipo.get(fila["tipo_mantenimiento"], fila["tipo_mantenimiento"])
        encabezados = ("Condominio", "Categoría", "Tipo", "Elemento", "Fecha", "Estado", "Descripción", "Empresa", "Próximo mantenimiento")
        filas = [(r.elemento.condominio.nombre, r.elemento.categoria, r.get_tipo_mantenimiento_display(),
                  r.elemento.nombre, r.fecha.isoformat(), r.get_estado_display(), r.descripcion, r.empresa,
                  r.proximo_mantenimiento.isoformat() if r.proximo_mantenimiento else "")
                 for r in registros.order_by("-fecha", "-id")]
        titulo = "Informe de mantenimiento"
        resumen_xlsx = [("Registros", registros.count()), ("Elementos", registros.values("elemento_id").distinct().count()),
                        ("Próximos", registros.filter(proximo_mantenimiento__gte=timezone.localdate()).count()),
                        ("Con fecha vencida", registros.filter(proximo_mantenimiento__lt=timezone.localdate()).count())]
        if exportar and not errores:
            salida = _entregar_exportacion(exportar, "mantenimiento_condominio.csv", titulo, subtitulo, encabezados, filas, resumen_xlsx)
            if salida:
                return salida
        contexto.update({"categorias": categorias, "categoria_filtro": categoria, "total": registros.count(), "resumen": resumen,
                         "registros": registros.order_by("-fecha", "-id")[:100], "tipo_nombre": "Historial de mantenimiento",
                         "mantenimientos_vencidos": registros.filter(proximo_mantenimiento__lt=timezone.localdate()).count(),
                         "columnas_resumen": ("Categoría", "Tipo", "Registros", "Elementos", "Próximas fechas", "Fechas vencidas")})

    elif tipo == "tickets":
        tickets = Ticket.objects.filter(condominio__in=condominios_filtro).select_related("condominio", "unidad", "unidad__edificio", "usuario", "asignado")
        if desde:
            tickets = tickets.filter(creado__date__gte=desde)
        if hasta:
            tickets = tickets.filter(creado__date__lte=hasta)
        estado = request.GET.get("estado", "").strip()
        if estado in Ticket.Estado.values:
            tickets = tickets.filter(estado=estado)
        elif estado:
            errores.append("El estado seleccionado no es válido.")
            estado = ""
        categoria_ticket = request.GET.get("categoria", "").strip()
        if categoria_ticket in Ticket.Categoria.values:
            tickets = tickets.filter(categoria=categoria_ticket)
        elif categoria_ticket:
            errores.append("La categoría seleccionada no es válida.")
            categoria_ticket = ""
        gestores = Usuario.objects.filter(
            rol__in=(Usuario.Rol.GESTOR, Usuario.Rol.ADMINISTRADOR), activo=True
        ).filter(Q(condominio__in=condominios_filtro) | Q(condominios_administrados__in=condominios_filtro)).distinct().order_by("nombre", "apellido")
        gestor_id = request.GET.get("gestor", "").strip()
        gestor = gestores.filter(pk=gestor_id).first() if gestor_id else None
        if gestor_id and not gestor:
            errores.append("El responsable seleccionado no pertenece a los condominios disponibles.")
            gestor_id = ""
        if gestor:
            tickets = tickets.filter(asignado=gestor)
        resumen = list(tickets.values("asignado__nombre", "asignado__apellido").annotate(
            total=Count("id"),
            abiertos=Count("id", filter=Q(estado__in=Ticket.ABIERTOS)),
            resueltos=Count("id", filter=Q(estado=Ticket.Estado.RESUELTO)),
            cerrados=Count("id", filter=Q(estado=Ticket.Estado.CERRADO)),
        ).order_by("asignado__apellido", "asignado__nombre"))
        encabezados = ("N.º", "Condominio", "Unidad", "Título", "Categoría", "Prioridad", "Estado", "Responsable asignado", "Reportado por", "Creado", "Último movimiento")
        filas = [(f"#{t.pk}", t.condominio.nombre, str(t.unidad) if t.unidad else "", t.titulo,
                  t.get_categoria_display(), t.get_prioridad_display() if t.prioridad else "Sin definir",
                  t.get_estado_display(), str(t.asignado) if t.asignado else "Sin asignar", str(t.usuario),
                  timezone.localtime(t.creado).strftime("%d/%m/%Y %H:%M"), timezone.localtime(t.actualizado).strftime("%d/%m/%Y %H:%M"))
                 for t in tickets.order_by("-actualizado", "-pk")]
        titulo = "Informe de tickets y seguimiento"
        resumen_xlsx = [("Tickets incluidos", tickets.count()),
                        ("Sin asignar", tickets.filter(asignado__isnull=True).count()),
                        ("Abiertos y en seguimiento", tickets.filter(estado__in=Ticket.ABIERTOS).count()),
                        ("Resueltos", tickets.filter(estado=Ticket.Estado.RESUELTO).count()),
                        ("Cerrados", tickets.filter(estado=Ticket.Estado.CERRADO).count()),
                        ("Gestor seleccionado", str(gestor) if gestor else "Todos")]
        if exportar and not errores:
            salida = _entregar_exportacion(exportar, "tickets_condominio.csv", titulo, subtitulo, encabezados, filas, resumen_xlsx)
            if salida:
                return salida
        contexto.update({"gestores": gestores, "gestor_filtro": gestor_id, "estados_ticket": Ticket.Estado.choices,
                         "estado_filtro": estado, "categorias_ticket": Ticket.Categoria.choices, "categoria_filtro": categoria_ticket,
                         "total": tickets.count(), "resumen": resumen, "registros": tickets.order_by("-actualizado", "-pk")[:100],
                         "tipo_nombre": "Tickets y desempeño de atención", "columnas_resumen": ("Responsable asignado", "Tickets", "Abiertos", "Resueltos", "Cerrados")})

    elif tipo in ("morosidad", "gastos"):
        periodos = Periodo.objects.filter(condominio__in=condominios_filtro, estado=Periodo.Estado.PUBLICADO).select_related("condominio").order_by("-anio", "-mes")
        periodos_condominio = list(periodos)
        periodos_por_condominio = {}
        for periodo in periodos_condominio:
            periodos_por_condominio.setdefault(periodo.condominio_id, periodo)
        periodo_id = request.GET.get("periodo", "").strip() if tipo == "gastos" else ""
        periodo = periodos.filter(pk=periodo_id).first() if periodo_id else None
        if periodo_id and not periodo:
            errores.append("El período seleccionado no está publicado o no pertenece a los condominios disponibles.")
            periodo_id = ""
        periodos_reporte = [periodo] if periodo else list(periodos_por_condominio.values())
        corte = ", ".join(f"{p.condominio.nombre}: {p.mes:02d}/{p.anio}" for p in periodos_reporte) or "Sin períodos publicados"
        subtitulo = f"Corte: {corte} · Generado el {timezone.localtime():%d/%m/%Y %H:%M}"

        if tipo == "morosidad":
            filas = []
            total_saldo = Decimal("0")
            total_principal = Decimal("0")
            total_interes = Decimal("0")
            calculo_intereses_disponible = True
            for periodo_corte in periodos_reporte:
                condominio = periodo_corte.condominio
                saldos = saldos_acumulados(condominio, hasta_periodo=periodo_corte)
                try:
                    moras = resumen_mora(condominio, timezone.localdate())
                except ValidationError:
                    moras = {}
                    calculo_intereses_disponible = False
                for unidad in Unidad.objects.filter(edificio__condominio=condominio).select_related("edificio").order_by("edificio__nombre", "piso", "posicion"):
                    saldo = Decimal(saldos.get(unidad.pk, 0))
                    mora = moras.get(unidad.pk, {})
                    principal = Decimal(mora.get("principal_vencido", 0))
                    interes = Decimal(mora.get("interes_devengado", 0)) if calculo_intereses_disponible else Decimal("0")
                    if saldo <= 0 and principal <= 0 and interes <= 0:
                        continue
                    filas.append((condominio.nombre, periodo_corte.anio, periodo_corte.mes,
                                  unidad.edificio.nombre, unidad.numero_unidad, saldo, principal, interes,
                                  "Vencido" if principal > 0 else "Interés devengado" if interes > 0 else "Saldo pendiente" if saldo > 0 else "Al día"))
                    total_saldo += saldo
                    total_principal += principal
                    total_interes += interes
            if not calculo_intereses_disponible:
                filas = [(*fila[:7], Decimal("0"), fila[8]) for fila in filas]
                total_interes = Decimal("0")
            filas.sort(key=lambda fila: (fila[0], -fila[6], -fila[5], fila[3], fila[4]))
            encabezados = ("Condominio", "Año de corte", "Mes de corte", "Edificio", "Unidad", "Saldo pendiente acumulado (CLP)", "Principal vencido (CLP)", "Interés devengado estimado (CLP)", "Situación")
            resumen = [{"situacion": "Vencidos", "unidades": sum(1 for f in filas if f[6] > 0), "saldo": total_principal},
                       {"situacion": "Con saldo pendiente", "unidades": sum(1 for f in filas if f[5] > 0), "saldo": total_saldo},
                       {"situacion": "Interés devengado estimado", "unidades": "—", "saldo": total_interes}]
            resumen_xlsx = [("Unidades con saldo pendiente", sum(1 for f in filas if f[5] > 0)),
                            ("Unidades con principal vencido", sum(1 for f in filas if f[6] > 0)),
                            ("Saldo pendiente acumulado (CLP)", total_saldo),
                            ("Principal vencido (CLP)", total_principal)]
            notas = ["El saldo acumulado resta los pagos registrados hasta el último período publicado de cada condominio. El principal vencido y los intereses se calculan a la fecha actual."]
            if calculo_intereses_disponible:
                resumen_xlsx.append(("Interés devengado estimado (CLP)", total_interes))
                notas.append("El interés estimado se calcula a la fecha actual según las tasas CMF ingresadas. Puede incluir interés ya facturado, incluido también en el saldo pendiente; no sumes ambas columnas como si fueran cargos independientes.")
            else:
                notas.append("No se pudo calcular interés para todas las unidades: faltan tasas CMF en uno o más tramos. El reporte conserva saldos/principal y no inventa intereses.")
            if exportar and not errores:
                salida = _entregar_exportacion(
                    exportar, "morosidad_condominio.csv", "Informe de morosidad y saldos",
                    subtitulo, encabezados, filas, resumen_xlsx, " ".join(notas),
                )
                if salida:
                    return salida
            contexto.update({"total": len(filas), "resumen": resumen,
                             "registros": filas[:100], "tipo_nombre": "Morosidad y saldos pendientes", "notas_reporte": notas,
                             "columnas_resumen": ("Situación", "Unidades", "Monto (CLP)"), "filas_financieras": True})
        else:
            detalles = DetalleCargo.objects.filter(liquidacion__periodo__in=periodos_reporte).select_related(
                "liquidacion__periodo__condominio", "liquidacion__unidad__edificio"
            )
            resumen = list(detalles.values("categoria").annotate(total=Sum("monto"), registros=Count("id")).order_by("categoria"))
            encabezados = ("Condominio", "Período", "Edificio", "Unidad", "Categoría", "Concepto", "Monto distribuido (CLP)")
            filas = [(d.liquidacion.periodo.condominio.nombre,
                      f"{d.liquidacion.periodo.mes:02d}/{d.liquidacion.periodo.anio}",
                      d.liquidacion.unidad.edificio.nombre, d.liquidacion.unidad.numero_unidad,
                      d.categoria, d.concepto, d.monto) for d in detalles.order_by(
                          "liquidacion__periodo__condominio__nombre", "liquidacion__periodo__anio", "liquidacion__periodo__mes",
                          "liquidacion__unidad__edificio__nombre", "liquidacion__unidad__piso", "liquidacion__unidad__posicion", "categoria", "concepto"
                      )]
            total_distribuido = sum((Decimal(f[-1]) for f in filas), Decimal("0"))
            resumen_xlsx = [("Cargos distribuidos", len(filas)), ("Total incluido en liquidaciones (CLP)", total_distribuido)]
            resumen_xlsx.extend((f"Categoría: {fila['categoria']}", fila["total"]) for fila in resumen)
            if exportar and not errores:
                salida = _entregar_exportacion(
                    exportar, "gastos_liquidados_condominio.csv", "Informe de gastos incluidos en liquidaciones",
                    subtitulo, encabezados, filas, resumen_xlsx,
                    "El detalle muestra montos distribuidos en liquidaciones. No es conciliación bancaria ni confirma pagos a proveedores.",
                )
                if salida:
                    return salida
            contexto.update({"periodos": periodos, "periodo_filtro": periodo_id, "total": len(filas), "resumen": resumen,
                             "registros": filas[:100], "tipo_nombre": "Gastos incluidos en liquidaciones", "filas_financieras": True,
                             "columnas_resumen": ("Categoría", "Cargos distribuidos", "Monto total (CLP)"),
                             "notas_reporte": ["El detalle muestra montos distribuidos en las liquidaciones. No representa conciliación bancaria ni confirma pagos a proveedores."]})

    contexto["errores"] = errores
    return render(request, "core/reportes.html", contexto)

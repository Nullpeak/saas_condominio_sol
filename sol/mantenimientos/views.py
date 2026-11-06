import os
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Condominio
from .models import DocumentoMantenimiento, ElementoMantenimiento, RegistroMantenimiento
from .validators import validar_documento


ROLES_PERMITIDOS = ("Super Administrador", "Administrador", "Gestor")
TIPOS_DOCUMENTO = DocumentoMantenimiento.Tipo.choices


def perfil_actual(request):
    return request.user.usuario


def condominios_visibles(usuario):
    return usuario.condominios_asignados().filter(activo=True).order_by("nombre")


def elementos_visibles(usuario):
    elementos = ElementoMantenimiento.objects.select_related("condominio", "creado_por").prefetch_related(
        "registros__documentos", "registros__creado_por"
    )
    if usuario.rol == "Super Administrador":
        return elementos
    condominios = condominios_visibles(usuario)
    if not condominios.exists():
        return elementos.none()
    return elementos.filter(condominio__in=condominios)


def puede_usar_modulo(usuario):
    return usuario.rol in ROLES_PERMITIDOS


def contexto_elemento_form(usuario, datos=None, errores=None, elemento=None):
    condominios = condominios_visibles(usuario)
    return {
        "usuario_actual": usuario,
        "condominios": condominios,
        "datos": datos or {},
        "errores": errores or [],
        "elemento": elemento,
        "condominio_unico": condominios.count() == 1,
    }


@login_required
def lista(request):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")

    condominios = condominios_visibles(usuario)
    if usuario.rol != "Super Administrador" and not condominios.exists():
        messages.error(request, "Tu cuenta todavía no tiene un condominio asignado.")
        return redirect("inicio")

    elementos = elementos_visibles(usuario)
    condominio_id = request.GET.get("condominio", "").strip()
    if condominio_id:
        elementos = elementos.filter(condominio_id=condominio_id)
        if not condominios.filter(pk=condominio_id).exists() and usuario.rol != "Super Administrador":
            elementos = elementos.none()
            condominio_id = ""
    categoria = request.GET.get("categoria", "").strip()
    if categoria:
        elementos = elementos.filter(categoria__iexact=categoria)

    elementos = list(elementos.order_by("categoria", "nombre"))
    for elemento in elementos:
        elemento.ultimo_registro = elemento.registros.first()
        elemento.proximo_mantenimiento = (
            elemento.ultimo_registro.proximo_mantenimiento if elemento.ultimo_registro else None
        )
        elemento.vencido = bool(
            elemento.proximo_mantenimiento and elemento.proximo_mantenimiento < timezone.localdate()
        )
    categorias = sorted(set(ElementoMantenimiento.objects.filter(
        pk__in=[item.pk for item in elementos_visibles(usuario)]
    ).values_list("categoria", flat=True)), key=str.casefold)

    return render(request, "mantenimientos/lista.html", {
        "usuario_actual": usuario,
        "elementos": elementos,
        "condominios": condominios,
        "condominio_filtro": condominio_id,
        "categorias": categorias,
        "categoria_filtro": categoria,
    })


@login_required
def crear_elemento(request):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")
    condominios = condominios_visibles(usuario)
    errores = []
    datos = request.POST if request.method == "POST" else {}

    if request.method == "POST":
        try:
            condominio_id = int(request.POST.get("condominio", ""))
        except (TypeError, ValueError):
            condominio_id = None
        condominio = Condominio.objects.filter(pk=condominio_id, activo=True).first() if condominio_id else None
        if usuario.rol != "Super Administrador" and (not condominio or not usuario.puede_acceder_condominio(condominio.pk)):
            errores.append("Selecciona un condominio al que tengas acceso.")
        elif usuario.rol == "Super Administrador" and not condominio:
            errores.append("Selecciona un condominio.")

        categoria = request.POST.get("categoria", "").strip()
        nombre = request.POST.get("nombre", "").strip()
        ubicacion = request.POST.get("ubicacion", "").strip()
        empresa = request.POST.get("empresa_actual", "").strip()
        telefono = request.POST.get("telefono_actual", "").strip()
        correo = request.POST.get("correo_actual", "").strip()
        notas = request.POST.get("notas", "").strip()
        if not categoria:
            errores.append("Escribe una categoría, por ejemplo piscina o ascensor.")
        if not nombre:
            errores.append("Indica el equipo o servicio, por ejemplo ascensor torre norte.")

        if not errores:
            elemento = ElementoMantenimiento(
                condominio=condominio,
                categoria=categoria,
                nombre=nombre,
                ubicacion=ubicacion,
                empresa_actual=empresa,
                telefono_actual=telefono,
                correo_actual=correo,
                notas=notas,
                creado_por=usuario,
            )
            try:
                elemento.full_clean()
                elemento.save()
            except ValidationError as error:
                errores.extend(error.messages)
            else:
                messages.success(request, "Se agregó al registro de mantenimiento.")
                return redirect("mantenimientos:detalle", elemento_id=elemento.pk)

    return render(request, "mantenimientos/elemento_form.html", contexto_elemento_form(
        usuario, datos, errores
    ))


@login_required
def editar_elemento(request, elemento_id):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")
    elemento = get_object_or_404(elementos_visibles(usuario), pk=elemento_id)
    errores = []
    datos = request.POST if request.method == "POST" else {
        "categoria": elemento.categoria,
        "nombre": elemento.nombre,
        "ubicacion": elemento.ubicacion,
        "empresa_actual": elemento.empresa_actual,
        "telefono_actual": elemento.telefono_actual,
        "correo_actual": elemento.correo_actual,
        "notas": elemento.notas,
    }
    if request.method == "POST":
        categoria = request.POST.get("categoria", "").strip()
        nombre = request.POST.get("nombre", "").strip()
        if not categoria:
            errores.append("Escribe una categoría, por ejemplo piscina o ascensor.")
        if not nombre:
            errores.append("Indica el equipo o servicio.")
        if not errores:
            elemento.categoria = categoria
            elemento.nombre = nombre
            elemento.ubicacion = request.POST.get("ubicacion", "").strip()
            elemento.empresa_actual = request.POST.get("empresa_actual", "").strip()
            elemento.telefono_actual = request.POST.get("telefono_actual", "").strip()
            elemento.correo_actual = request.POST.get("correo_actual", "").strip()
            elemento.notas = request.POST.get("notas", "").strip()
            try:
                elemento.full_clean()
                elemento.save()
            except ValidationError as error:
                errores.extend(error.messages)
            else:
                messages.success(request, "Se actualizaron los datos del equipo o servicio.")
                return redirect("mantenimientos:detalle", elemento_id=elemento.pk)
    return render(request, "mantenimientos/elemento_form.html", contexto_elemento_form(
        usuario, datos, errores, elemento
    ))


@login_required
def detalle(request, elemento_id):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")
    elemento = get_object_or_404(elementos_visibles(usuario), pk=elemento_id)
    errores = []
    if request.method == "POST":
        fecha_texto = request.POST.get("fecha", "").strip()
        proxima_texto = request.POST.get("proximo_mantenimiento", "").strip()
        descripcion = request.POST.get("descripcion", "").strip()
        estado = request.POST.get("estado", RegistroMantenimiento.Estado.PROGRAMADO)
        detalle_cierre = request.POST.get("detalle_cierre", "").strip()
        empresa = request.POST.get("empresa", "").strip()
        telefono = request.POST.get("telefono_empresa", "").strip()
        archivos = request.FILES.getlist("documentos")
        tipo_documento = request.POST.get("tipo_documento", DocumentoMantenimiento.Tipo.OTRO)
        nombre_documento = request.POST.get("nombre_documento", "").strip()

        try:
            fecha = date.fromisoformat(fecha_texto)
        except ValueError:
            fecha = None
            errores.append("Indica una fecha válida para el mantenimiento.")
        try:
            proxima = date.fromisoformat(proxima_texto) if proxima_texto else None
        except ValueError:
            proxima = None
            errores.append("La próxima fecha de mantenimiento no es válida.")
        if fecha and proxima and proxima < fecha:
            errores.append("La próxima fecha debe ser igual o posterior a la fecha de este servicio.")
        if estado not in RegistroMantenimiento.Estado.values:
            errores.append("Selecciona un estado válido.")
        if not descripcion:
            errores.append("Describe el mantenimiento programado o el trabajo realizado.")
        if estado == RegistroMantenimiento.Estado.COMPLETADO and not detalle_cierre:
            errores.append("Al completar el mantenimiento, indica qué trabajo se realizó.")
        if tipo_documento not in DocumentoMantenimiento.Tipo.values:
            errores.append("Selecciona un tipo de documento válido.")
        if len(nombre_documento) > 150:
            errores.append("El nombre del documento no puede superar 150 caracteres.")
        if len(archivos) > 10:
            errores.append("Puedes adjuntar hasta 10 documentos en cada registro.")
        for archivo in archivos:
            try:
                validar_documento(archivo)
            except ValidationError as error:
                errores.extend(error.messages)

        if not errores:
            registro = RegistroMantenimiento(
                elemento=elemento,
                fecha=fecha,
                descripcion=descripcion,
                estado=estado,
                detalle_cierre=detalle_cierre,
                empresa=empresa,
                telefono_empresa=telefono,
                proximo_mantenimiento=proxima,
                creado_por=usuario,
            )
            try:
                registro.full_clean()
            except ValidationError as error:
                errores.extend(error.messages)
            else:
                with transaction.atomic():
                    registro.save()
                    for archivo in archivos:
                        documento = DocumentoMantenimiento(
                            registro=registro,
                            tipo=tipo_documento,
                            nombre=nombre_documento or os.path.basename(archivo.name)[:150],
                            archivo=archivo,
                            subido_por=usuario,
                        )
                        documento.full_clean()
                        documento.save()
                    if empresa or telefono:
                        elemento.empresa_actual = empresa
                        elemento.telefono_actual = telefono
                        elemento.save(update_fields=["empresa_actual", "telefono_actual", "actualizado"])
                messages.success(request, "Se guardó el mantenimiento y sus documentos.")
                return redirect("mantenimientos:detalle", elemento_id=elemento.pk)

    return render(request, "mantenimientos/detalle.html", {
        "usuario_actual": usuario,
        "elemento": elemento,
        "registros": elemento.registros.select_related("creado_por").prefetch_related("documentos"),
        "errores": errores,
        "datos": request.POST if request.method == "POST" else {
            "empresa": elemento.empresa_actual,
            "telefono_empresa": elemento.telefono_actual,
        },
        "tipos_documento": TIPOS_DOCUMENTO,
        "estados_mantenimiento": RegistroMantenimiento.Estado.choices,
        "hoy": timezone.localdate().isoformat(),
    })


@login_required
def editar_registro(request, registro_id):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")
    registro = get_object_or_404(
        RegistroMantenimiento.objects.select_related("elemento"),
        pk=registro_id, elemento__in=elementos_visibles(usuario),
    )
    if registro.estado == RegistroMantenimiento.Estado.COMPLETADO:
        messages.error(request, "Los registros completados se conservan como historial y no se pueden editar.")
        return redirect("mantenimientos:detalle", elemento_id=registro.elemento_id)
    errores = []
    datos = request.POST if request.method == "POST" else {
        "fecha": registro.fecha.isoformat(), "estado": registro.estado,
        "proximo_mantenimiento": registro.proximo_mantenimiento.isoformat() if registro.proximo_mantenimiento else "",
        "empresa": registro.empresa, "telefono_empresa": registro.telefono_empresa,
        "descripcion": registro.descripcion, "detalle_cierre": registro.detalle_cierre,
    }
    if request.method == "POST":
        try:
            fecha = date.fromisoformat(request.POST.get("fecha", ""))
        except ValueError:
            fecha = None
            errores.append("Indica una fecha válida.")
        try:
            proxima = date.fromisoformat(request.POST.get("proximo_mantenimiento", "")) if request.POST.get("proximo_mantenimiento", "").strip() else None
        except ValueError:
            proxima = None
            errores.append("La próxima fecha no es válida.")
        estado = request.POST.get("estado", "")
        descripcion = request.POST.get("descripcion", "").strip()
        detalle_cierre = request.POST.get("detalle_cierre", "").strip()
        if estado not in RegistroMantenimiento.Estado.values:
            errores.append("Selecciona un estado válido.")
        if not descripcion:
            errores.append("Describe el mantenimiento.")
        if estado == RegistroMantenimiento.Estado.COMPLETADO and not detalle_cierre:
            errores.append("Para completar el mantenimiento, indica el trabajo realizado.")
        if fecha and proxima and proxima < fecha:
            errores.append("La próxima fecha debe ser igual o posterior a la fecha del mantenimiento.")
        if not errores:
            registro.fecha = fecha
            registro.proximo_mantenimiento = proxima
            registro.estado = estado
            registro.descripcion = descripcion
            registro.detalle_cierre = detalle_cierre
            registro.empresa = request.POST.get("empresa", "").strip()
            registro.telefono_empresa = request.POST.get("telefono_empresa", "").strip()
            registro.full_clean()
            registro.save()
            messages.success(request, "Se actualizaron los datos del mantenimiento.")
            return redirect("mantenimientos:detalle", elemento_id=registro.elemento_id)
    return render(request, "mantenimientos/registro_form.html", {
        "registro": registro, "datos": datos, "errores": errores,
        "estados": RegistroMantenimiento.Estado.choices,
    })


@login_required
def descargar_documento(request, documento_id):
    usuario = perfil_actual(request)
    if not puede_usar_modulo(usuario):
        return redirect("inicio")
    documento = get_object_or_404(
        DocumentoMantenimiento.objects.select_related("registro__elemento__condominio"),
        pk=documento_id,
    )
    if not elementos_visibles(usuario).filter(pk=documento.registro.elemento_id).exists():
        messages.error(request, "No tienes acceso a este documento.")
        return redirect("mantenimientos:lista")
    return FileResponse(
        documento.archivo.open("rb"),
        as_attachment=True,
        filename=documento.nombre or os.path.basename(documento.archivo.name),
    )

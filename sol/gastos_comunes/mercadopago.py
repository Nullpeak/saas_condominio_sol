"""Integración de servidor con Checkout Pro Orders, limitada a credenciales de prueba."""
import hashlib
import hmac
import json
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PagoOnline, PagoRegistrado


API_URL = "https://api.mercadopago.com/v1/orders"


class MercadoPagoError(Exception):
    """Error seguro para mostrar en la interfaz sin filtrar credenciales."""


def _token():
    token = getattr(settings, "MERCADOPAGO_TEST_ACCESS_TOKEN", "")
    if not token:
        raise MercadoPagoError("Falta configurar la credencial de prueba de Mercado Pago.")
    return token


def _api_request(url, method="GET", body=None, idempotency_key=None):
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Idempotency-Key"] = str(idempotency_key)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise MercadoPagoError("Mercado Pago devolvió una respuesta no válida.")
            return payload
    except MercadoPagoError:
        raise
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise MercadoPagoError("Mercado Pago no respondió correctamente. Puedes volver a intentarlo.") from exc


def crear_orden(pago, urls):
    usuario = pago.usuario
    unidad = pago.liquidacion.unidad
    periodo = pago.liquidacion.periodo
    monto = int(pago.monto)
    descripcion = (
        f"Gastos comunes {periodo.mes:02d}/{periodo.anio} · "
        f"{unidad.edificio.nombre} unidad {unidad.numero_unidad}"
    )[:250]
    orden = _api_request(
        API_URL,
        method="POST",
        idempotency_key=pago.clave_idempotencia,
        body={
            "type": "online",
            "processing_mode": "manual",
            "capture_mode": "automatic_async",
            "total_amount": str(monto),
            "external_reference": str(pago.referencia_externa),
            "description": descripcion,
            "payer": {"email": usuario.email},
            "items": [{"title": descripcion, "quantity": 1, "unit_price": str(monto)}],
            "config": {"online": {
                "success_url": urls["success"],
                "failure_url": urls["failure"],
                "pending_url": urls["pending"],
                "auto_return": "all",
            }},
        },
    )
    order_id = str(orden.get("id") or "")
    checkout_url = str(orden.get("checkout_url") or "")
    parsed_url = urlparse(checkout_url)
    host = (parsed_url.hostname or "").lower()
    if orden.get("currency") != "CLP":
        raise MercadoPagoError("La cuenta de prueba debe estar configurada para operar en pesos chilenos.")
    if not order_id or parsed_url.scheme != "https" or not (host == "mercadopago.cl" or host.endswith(".mercadopago.cl")):
        raise MercadoPagoError("Mercado Pago devolvió una dirección de pago no válida.")
    pago.orden_proveedor = order_id
    pago.url_pago = checkout_url
    pago.estado = PagoOnline.Estado.PENDIENTE
    pago.detalle_estado = str(orden.get("status_detail") or "created")[:100]
    pago.save(update_fields=["orden_proveedor", "url_pago", "estado", "detalle_estado", "actualizado"])
    return checkout_url


def verificar_firma_webhook(request, secret):
    """Valida la firma HMAC publicada por Mercado Pago para la notificación."""
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    parts = dict(
        piece.split("=", 1) for piece in signature.split(",") if "=" in piece
    )
    timestamp = parts.get("ts", "")
    received_hash = parts.get("v1", "")
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    data = payload.get("data")
    data_id = request.GET.get("data.id") or (data.get("id") if isinstance(data, dict) else "") or ""
    if not secret or not timestamp or not received_hash or not request_id or not data_id:
        return False
    manifest = f"id:{str(data_id).lower()};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(secret.encode("utf-8"), manifest.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)


@transaction.atomic
def sincronizar_pago(pago):
    """Consulta la order directamente a Mercado Pago y registra una sola vez el abono."""
    pago = PagoOnline.objects.select_for_update().select_related(
        "usuario", "liquidacion", "liquidacion__unidad", "liquidacion__periodo"
    ).get(pk=pago.pk)
    if pago.estado == PagoOnline.Estado.APROBADO:
        return pago
    if not pago.orden_proveedor:
        return pago

    orden = _api_request(f"{API_URL}/{pago.orden_proveedor}")
    if str(orden.get("id")) != pago.orden_proveedor:
        raise MercadoPagoError("La orden recibida no coincide con este intento de pago.")
    if str(orden.get("external_reference") or "") != str(pago.referencia_externa):
        raise MercadoPagoError("La referencia de la orden no coincide con el pago.")
    try:
        total_orden = Decimal(str(orden.get("total_amount") or "0"))
        total_pagado = Decimal(str(orden.get("total_paid_amount") or "0"))
    except InvalidOperation as exc:
        raise MercadoPagoError("Mercado Pago devolvió un monto no válido.") from exc

    estado = str(orden.get("status") or "").lower()
    detalle = str(orden.get("status_detail") or estado)[:100]
    if orden.get("currency") != "CLP" or total_orden != pago.monto:
        raise MercadoPagoError("La moneda o el monto de la orden no coincide con la liquidación.")
    if estado == "processed" and total_pagado == pago.monto:
        pago.estado = PagoOnline.Estado.APROBADO
        pago.pagado = timezone.now()
        PagoRegistrado.objects.get_or_create(
            pago_online=pago,
            defaults={
                "liquidacion": pago.liquidacion,
                "monto": pago.monto,
                "fecha": timezone.localdate(),
                "metodo": "Mercado Pago (prueba)",
                "referencia": pago.orden_proveedor,
                "registrado_por": pago.usuario,
            },
        )
    elif estado in {"cancelled", "canceled"}:
        pago.estado = PagoOnline.Estado.CANCELADO
    elif estado in {"failed", "rejected"}:
        pago.estado = PagoOnline.Estado.RECHAZADO
    else:
        pago.estado = PagoOnline.Estado.PENDIENTE
    pago.detalle_estado = detalle
    pago.save(update_fields=["estado", "detalle_estado", "pagado", "actualizado"])
    return pago

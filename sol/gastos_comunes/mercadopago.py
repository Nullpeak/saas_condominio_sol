"""OAuth vendedor y Checkout Pro para Split 1:1 de Mercado Pago Chile"""
import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import MercadoPagoConexion, PagoOnline, PagoRegistrado

API = "https://api.mercadopago.com"


class MercadoPagoError(Exception):
    """Error seguro para mostrar sin credenciales ni respuestas privadas."""


def es_modo_prueba():
    entorno = getattr(settings, "MERCADOPAGO_ENVIRONMENT", "test").strip().lower()
    if entorno in {"test", "sandbox"}:
        return True
    if entorno in {"production", "live"}:
        return False
    raise MercadoPagoError("MERCADOPAGO_ENVIRONMENT debe ser 'test' o 'production'.")


def secreto_webhook_actual():
    setting = "MERCADOPAGO_TEST_WEBHOOK_SECRET" if es_modo_prueba() else "MERCADOPAGO_WEBHOOK_SECRET"
    return getattr(settings, setting, "")


def _fernet():
    key = getattr(settings, "MERCADOPAGO_TOKEN_ENCRYPTION_KEY", "")
    if not key:
        raise MercadoPagoError("Falta configurar la clave de cifrado de Mercado Pago.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise MercadoPagoError("La clave de cifrado de Mercado Pago no tiene un formato válido.") from exc


def _cifrar(value):
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _descifrar(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise MercadoPagoError("No fue posible leer de forma segura la conexión de Mercado Pago.") from exc


def _oauth_config():
    client_id = getattr(settings, "MERCADOPAGO_CLIENT_ID", "")
    client_secret = getattr(settings, "MERCADOPAGO_CLIENT_SECRET", "")
    redirect_uri = getattr(settings, "MERCADOPAGO_REDIRECT_URI", "")
    if not all((client_id, client_secret, redirect_uri)):
        raise MercadoPagoError("Falta configurar la aplicación Marketplace de Mercado Pago.")
    if not redirect_uri.startswith("https://"):
        raise MercadoPagoError("La URL de retorno OAuth debe usar HTTPS.")
    return client_id, client_secret, redirect_uri


def crear_intento_oauth(condominio, usuario):
    from .models import MercadoPagoIntentoOAuth
    client_id, _, redirect_uri = _oauth_config()
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    modo_prueba = es_modo_prueba()
    intento = MercadoPagoIntentoOAuth.objects.create(
        condominio=condominio, usuario=usuario,
        state_hash=hashlib.sha256(state.encode()).hexdigest(),
        state_cifrado=_cifrar(state),
        verifier_cifrado=_cifrar(verifier),
        modo_prueba=modo_prueba,
    )
    params = {
        "client_id": client_id, "response_type": "code", "platform_id": "mp",
        "redirect_uri": redirect_uri, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return intento, "https://auth.mercadopago.cl/authorization?" + urlencode(params)


def _request(url, method="GET", token=None, body=None, form=False, idempotency_key=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        if form:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urlencode(body).encode()
        else:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
    if idempotency_key:
        headers["X-Idempotency-Key"] = str(idempotency_key)
    try:
        req = Request(url, data=data, headers=headers, method=method)
        with urlopen(req, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise MercadoPagoError("Mercado Pago devolvió una respuesta no válida.")
            return payload
    except MercadoPagoError:
        raise
    except HTTPError as exc:
        # Conservar sólo códigos técnicos de Mercado Pago; nunca guardar ni
        # mostrar cuerpos completos que podrían contener datos sensibles.
        try:
            error_payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            error_payload = {}
        codes = []
        if isinstance(error_payload, dict):
            causes = error_payload.get("cause")
            if not isinstance(causes, list):
                causes = []
            candidates = [error_payload.get("error")] + [
                cause.get("code") for cause in causes if isinstance(cause, dict)
            ]
            for candidate in candidates:
                if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", candidate):
                    if candidate not in codes:
                        codes.append(candidate)
        detail = f" Códigos: {', '.join(codes[:3])}." if codes else ""
        raise MercadoPagoError(f"Mercado Pago rechazó la solicitud (HTTP {exc.code}).{detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        # No exponer body de error, URL con parámetros, ni credenciales.
        raise MercadoPagoError("Mercado Pago no respondió correctamente. Inténtalo nuevamente.") from exc


def completar_oauth(intento, code):
    client_id, client_secret, redirect_uri = _oauth_config()
    verifier = _descifrar(intento.verifier_cifrado)
    payload = _request(API + "/oauth/token", "POST", body={
        "client_id": client_id, "client_secret": client_secret,
        "grant_type": "authorization_code", "code": code,
        "redirect_uri": redirect_uri, "code_verifier": verifier,
        "state": _descifrar(intento.state_cifrado),
        "test_token": "true" if intento.modo_prueba else "false",
    }, form=True)
    if not all(payload.get(k) for k in ("access_token", "refresh_token", "user_id")):
        raise MercadoPagoError("Mercado Pago no devolvió los permisos de vendedor esperados.")
    if "live_mode" in payload and bool(payload["live_mode"]) == intento.modo_prueba:
        raise MercadoPagoError("Mercado Pago devolvió credenciales de un ambiente distinto al seleccionado.")
    vendedor_id = str(payload["user_id"])
    if MercadoPagoConexion.objects.filter(vendedor_id=vendedor_id).exclude(condominio=intento.condominio).exists():
        raise MercadoPagoError(
            "Esta cuenta vendedora ya está vinculada a otro condominio. "
            "Cambia a la cuenta vendedora designada para este condominio en Mercado Pago y vuelve a iniciar la conexión."
        )
    with transaction.atomic():
        MercadoPagoConexion.objects.update_or_create(
            condominio=intento.condominio,
            defaults={
                "vendedor_id": vendedor_id,
                "public_key": str(payload.get("public_key") or ""),
                "access_token_cifrado": _cifrar(payload["access_token"]),
                "refresh_token_cifrado": _cifrar(payload["refresh_token"]),
                "modo_prueba": intento.modo_prueba,
                "vence": timezone.now() + timedelta(seconds=int(payload.get("expires_in") or 0)),
                "conectado_por": intento.usuario,
            },
        )
        intento.delete()


def token_vendedor(condominio):
    conexion = MercadoPagoConexion.objects.filter(condominio=condominio).first()
    if not conexion:
        raise MercadoPagoError("Este condominio aún no tiene una cuenta vendedora conectada.")
    if conexion.modo_prueba != es_modo_prueba():
        raise MercadoPagoError("La cuenta conectada pertenece a otro ambiente. Desconéctala y autoriza una cuenta del ambiente seleccionado.")
    # Renovar con anticipación; la respuesta OAuth rota ambos tokens.
    if conexion.vence <= timezone.now() + timedelta(minutes=5):
        client_id, client_secret, _ = _oauth_config()
        payload = _request(API + "/oauth/token", "POST", body={
            "client_id": client_id, "client_secret": client_secret,
            "grant_type": "refresh_token", "refresh_token": _descifrar(conexion.refresh_token_cifrado),
            "test_token": "true" if conexion.modo_prueba else "false",
        }, form=True)
        if not all(payload.get(k) for k in ("access_token", "refresh_token")):
            raise MercadoPagoError("La autorización de Mercado Pago venció; vuelve a conectar la cuenta.")
        conexion.access_token_cifrado = _cifrar(payload["access_token"])
        conexion.refresh_token_cifrado = _cifrar(payload["refresh_token"])
        if "live_mode" in payload and bool(payload["live_mode"]) == conexion.modo_prueba:
            raise MercadoPagoError("La renovación devolvió credenciales de un ambiente distinto al seleccionado.")
        conexion.vence = timezone.now() + timedelta(seconds=int(payload.get("expires_in") or 0))
        conexion.save(update_fields=["access_token_cifrado", "refresh_token_cifrado", "vence", "conectado"])
    return conexion, _descifrar(conexion.access_token_cifrado)


def crear_checkout(pago, urls):
    conexion, token = token_vendedor(pago.liquidacion.periodo.condominio)
    unidad = pago.liquidacion.unidad
    periodo = pago.liquidacion.periodo
    descripcion = f"Gastos comunes {periodo.mes:02d}/{periodo.anio} · {unidad.edificio.nombre} unidad {unidad.numero_unidad}"[:250]
    body = {
        "items": [{"id": str(pago.referencia_externa), "title": descripcion,
                   "quantity": 1, "currency_id": "CLP", "unit_price": float(pago.monto)}],
        "external_reference": str(pago.referencia_externa),
        "back_urls": {"success": urls["success"], "failure": urls["failure"], "pending": urls["pending"]},
        "auto_return": "approved", "notification_url": urls["notification"],
    }
    # No enviar correos reales al sandbox; allí el comprador inicia sesión con
    # la cuenta de prueba creada en Tus integraciones.
    if not conexion.modo_prueba:
        body["payer"] = {"email": pago.usuario.email}
    preference = _request(API + "/checkout/preferences", "POST", token=token, idempotency_key=pago.clave_idempotencia, body=body)
    checkout_url = str(preference.get("sandbox_init_point" if conexion.modo_prueba else "init_point") or "")
    preference_id = str(preference.get("id") or "")
    from urllib.parse import urlparse
    parsed = urlparse(checkout_url)
    host = (parsed.hostname or "").lower()
    dominio_valido = any(
        host == dominio or host.endswith("." + dominio)
        for dominio in ("mercadopago.cl", "mercadopago.com")
    )
    if not preference_id or parsed.scheme != "https" or not dominio_valido:
        raise MercadoPagoError("Mercado Pago devolvió una dirección de pago no válida.")
    pago.preferencia_proveedor = preference_id
    pago.url_pago = checkout_url
    pago.estado = PagoOnline.Estado.PENDIENTE
    pago.detalle_estado = "preference_created"
    pago.save(update_fields=["preferencia_proveedor", "url_pago", "estado", "detalle_estado", "actualizado"])
    return checkout_url


def verificar_firma_webhook(request, secret):
    signature = request.headers.get("x-signature", "")
    request_id = request.headers.get("x-request-id", "")
    parts = dict(piece.split("=", 1) for piece in signature.split(",") if "=" in piece)
    timestamp, received_hash = parts.get("ts", ""), parts.get("v1", "")
    data_id = request.GET.get("data.id", "")
    if not secret or not timestamp or not received_hash or not request_id or not data_id:
        return False
    manifest = f"id:{data_id.lower()};request-id:{request_id};ts:{timestamp};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_hash)


def sincronizar_pago(pago, payment_id=None, order_id=None):
    pago_id = pago.pk
    pago = PagoOnline.objects.select_related("liquidacion__periodo__condominio", "usuario", "liquidacion__unidad").get(pk=pago_id)
    if pago.estado == PagoOnline.Estado.APROBADO:
        return pago
    order_id = str(order_id or (pago.orden_proveedor if pago.orden_proveedor and not pago.orden_proveedor.isdigit() else ""))
    conexion, token = token_vendedor(pago.liquidacion.periodo.condominio)
    if order_id:
        order = _request(f"{API}/v1/orders/{order_id}", token=token)
        if str(order.get("id") or "") != order_id:
            raise MercadoPagoError("La orden recibida no coincide con este intento.")
        try:
            amount = Decimal(str(order.get("total_amount") or "0"))
        except InvalidOperation as exc:
            raise MercadoPagoError("Mercado Pago devolvió un monto no válido.") from exc
        valid = (
            str(order.get("external_reference") or "") == str(pago.referencia_externa)
            and str(order.get("user_id") or "") == conexion.vendedor_id
            and order.get("currency") == "CLP"
            and amount == pago.monto
        )
        status = str(order.get("status") or "").lower()
        detail = str(order.get("status_detail") or status)
        transactions = (order.get("transactions") or {}).get("payments") or []
        provider_reference = str((transactions[0] if transactions else {}).get("id") or order_id)
        approved = valid and status == "processed" and detail == "accredited"
        provider_id = order_id
        cancelled = status in {"canceled", "refunded", "charged_back"}
        rejected = status == "failed"
    else:
        payment_id = str(payment_id or (pago.orden_proveedor if pago.orden_proveedor.isdigit() else ""))
        if not payment_id.isdigit():
            return pago
        payment = _request(f"{API}/v1/payments/{payment_id}", token=token)
        if str(payment.get("id")) != payment_id:
            raise MercadoPagoError("El pago recibido no coincide con este intento.")
        try:
            amount = Decimal(str(payment.get("transaction_amount") or "0"))
        except InvalidOperation as exc:
            raise MercadoPagoError("Mercado Pago devolvió un monto no válido.") from exc
        valid = (
            str(payment.get("external_reference") or "") == str(pago.referencia_externa)
            and str(payment.get("collector_id") or "") == conexion.vendedor_id
            and payment.get("currency_id") == "CLP"
            and amount == pago.monto
        )
        status = str(payment.get("status") or "").lower()
        detail = str(payment.get("status_detail") or status)
        provider_reference = payment_id
        provider_id = payment_id
        approved = valid and status == "approved"
        cancelled = status in {"cancelled", "refunded", "charged_back"}
        rejected = status == "rejected"
    if not valid:
        raise MercadoPagoError("Los datos del pago no coinciden con la liquidación o la cuenta destino.")
    estado = PagoOnline.Estado.APROBADO if approved else PagoOnline.Estado.CANCELADO if cancelled else PagoOnline.Estado.RECHAZADO if rejected else PagoOnline.Estado.PENDIENTE
    with transaction.atomic():
        pago = PagoOnline.objects.select_for_update().select_related("liquidacion", "usuario").get(pk=pago_id)
        if pago.estado == PagoOnline.Estado.APROBADO:
            return pago
        pago.orden_proveedor = provider_id
        pago.estado = estado
        pago.detalle_estado = detail[:100]
        if approved:
            PagoRegistrado.objects.get_or_create(
                pago_online=pago,
                defaults={"liquidacion": pago.liquidacion, "monto": pago.monto,
                          "fecha": timezone.localdate(), "metodo": "Mercado Pago",
                          "referencia": provider_reference, "registrado_por": pago.usuario},
            )
            pago.pagado = timezone.now()
        pago.save(update_fields=["orden_proveedor", "estado", "detalle_estado", "pagado", "actualizado"])
        return pago


def localizar_pago_webhook(payment_id=None, order_id=None):
    """Resuelve el pago consultando al vendedor OAuth; no confía en el cuerpo del webhook."""
    for conexion in MercadoPagoConexion.objects.select_related("condominio").iterator():
        try:
            conexion_actual, token = token_vendedor(conexion.condominio)
            if order_id:
                resource = _request(f"{API}/v1/orders/{order_id}", token=token)
                seller_id = str(resource.get("user_id") or "")
            else:
                resource = _request(f"{API}/v1/payments/{payment_id}", token=token)
                seller_id = str(resource.get("collector_id") or "")
        except MercadoPagoError:
            continue
        if seller_id != conexion_actual.vendedor_id:
            continue
        referencia = str(resource.get("external_reference") or "")
        pago = PagoOnline.objects.filter(
            referencia_externa=referencia,
            liquidacion__periodo__condominio=conexion.condominio,
        ).first()
        if pago:
            return pago
    return None

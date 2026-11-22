"""Cliente REST mínimo para Webpay Plus con credenciales por condominio.

Se usa el API REST oficial para conservar compatibilidad con Python 3.11 del
proyecto; la versión actual del SDK oficial requiere Python 3.12 o posterior.
"""
import json
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings


API_PATH = "/rswebpaytransaction/api/webpay/v1.2/transactions"
TEST_COMMERCE_CODE = "597055555532"
TEST_API_KEY = "579B532A7440BB0C9079DED94D31EA1615BACEB56610332264630D42D0A36B1C"


class WebpayError(Exception):
    """Error de integración seguro para mostrar en la interfaz."""


def _fernet():
    key = (
        getattr(settings, "PAYMENT_CREDENTIALS_ENCRYPTION_KEY", "")
        or getattr(settings, "MERCADOPAGO_TOKEN_ENCRYPTION_KEY", "")
    )
    if not key:
        raise WebpayError(
            "Falta configurar PAYMENT_CREDENTIALS_ENCRYPTION_KEY para proteger la llave de Transbank."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise WebpayError("La clave de cifrado configurada no tiene un formato válido.") from exc


def cifrar_api_key(value):
    try:
        return _fernet().encrypt(value.encode("utf-8")).decode("ascii")
    except WebpayError:
        raise


def _descifrar_api_key(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise WebpayError("No fue posible descifrar la llave de Transbank.") from exc


def credenciales(conexion):
    if conexion.modo_prueba:
        return TEST_COMMERCE_CODE, TEST_API_KEY, "https://webpay3gint.transbank.cl"
    if not conexion.codigo_comercio or not conexion.api_key_cifrada:
        raise WebpayError("Este condominio no tiene credenciales de producción de Webpay configuradas.")
    return (
        conexion.codigo_comercio.strip(),
        _descifrar_api_key(conexion.api_key_cifrada),
        "https://webpay3g.transbank.cl",
    )


def _request(conexion, method, path, payload=None):
    commerce_code, api_key, host = credenciales(conexion)
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        host + API_PATH + path,
        data=body,
        method=method,
        headers={
            "Tbk-Api-Key-Id": commerce_code,
            "Tbk-Api-Key-Secret": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        # No guardar ni propagar el cuerpo: puede incluir información de diagnóstico.
        raise WebpayError(f"Transbank rechazó la solicitud (HTTP {exc.code}).") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise WebpayError("No fue posible comunicarse con Transbank. Revisa el estado del intento antes de repetirlo.") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise WebpayError("Transbank devolvió una respuesta que no se pudo interpretar.") from exc
    if not isinstance(result, dict):
        raise WebpayError("Transbank devolvió una respuesta no válida.")
    return result


def crear_transaccion(conexion, pago, return_url):
    response = _request(conexion, "POST", "", {
        "buy_order": pago.orden_proveedor,
        "session_id": str(pago.referencia_externa),
        "amount": int(pago.monto),
        "return_url": return_url,
    })
    token, url = response.get("token"), response.get("url")
    host = urlparse(str(url or "")).hostname or ""
    if not token or not url or not str(url).startswith("https://") or not host.lower().endswith(".transbank.cl"):
        raise WebpayError("Transbank no devolvió el acceso seguro al formulario de pago.")
    return str(token), str(url)


def confirmar_transaccion(conexion, token):
    return _request(conexion, "PUT", f"/{token}")


def consultar_transaccion(conexion, token):
    return _request(conexion, "GET", f"/{token}")


def actualizar_estado_pago(pago, resultado):
    """Actualiza solo tras validar contra los datos locales de la orden."""
    from django.db import transaction
    from django.utils import timezone

    from .models import PagoOnline, PagoRegistrado

    try:
        monto = int(resultado.get("amount"))
    except (TypeError, ValueError):
        raise WebpayError("El monto informado por Transbank no es válido.")
    if monto != int(pago.monto) or resultado.get("buy_order") != pago.orden_proveedor:
        raise WebpayError("La confirmación de Transbank no coincide con el monto y la orden local.")

    raw_response_code = resultado.get("response_code")
    try:
        response_code = int(raw_response_code) if raw_response_code is not None else None
    except (TypeError, ValueError):
        response_code = None
    aprobado = resultado.get("status") == "AUTHORIZED" and response_code == 0
    rechazado = resultado.get("status") in {"FAILED", "REVERSED", "NULLIFIED"} or (
        response_code is not None and response_code != 0
    )
    with transaction.atomic():
        pago = PagoOnline.objects.select_for_update().select_related("liquidacion", "usuario").get(pk=pago.pk)
        if pago.estado == PagoOnline.Estado.APROBADO:
            return pago
        if aprobado:
            pago.estado = PagoOnline.Estado.APROBADO
            pago.detalle_estado = "webpay_authorized"
            pago.codigo_autorizacion = str(resultado.get("authorization_code") or "")[:20]
            pago.pagado = timezone.now()
            pago.save(update_fields=["estado", "detalle_estado", "codigo_autorizacion", "pagado", "actualizado"])
            PagoRegistrado.objects.get_or_create(
                pago_online=pago,
                defaults={
                    "liquidacion": pago.liquidacion,
                    "monto": pago.monto,
                    "fecha": timezone.localdate(),
                    "metodo": "Webpay Plus",
                    "referencia": pago.codigo_autorizacion or str(pago.orden_proveedor),
                    "registrado_por": pago.usuario,
                },
            )
        elif rechazado:
            pago.estado = PagoOnline.Estado.RECHAZADO
            pago.detalle_estado = "webpay_rejected"
            pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
        else:
            pago.estado = PagoOnline.Estado.PENDIENTE
            pago.detalle_estado = "webpay_pending"
            pago.save(update_fields=["estado", "detalle_estado", "actualizado"])
    return pago


def validar_codigo_comercio(codigo):
    return codigo.isdigit() and 8 <= len(codigo) <= 20


def validar_api_key(api_key):
    return len(api_key) >= 32 and len(api_key) <= 256

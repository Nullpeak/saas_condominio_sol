import re

import phonenumbers
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

#============================== VALIDACIÓN RUT ==============================

def limpiar_rut(rut):
    """Deja solo dígitos y K en mayúscula."""
    return re.sub(r"[.\s-]", "", rut).upper()


def calcular_dv(cuerpo):
    """Calcula el dígito verificador mediante módulo 11."""
    suma = 0
    multiplicador = 2

    for digito in reversed(cuerpo):
        suma += int(digito) * multiplicador
        multiplicador = 2 if multiplicador == 7 else multiplicador + 1

    resto = 11 - (suma % 11)

    if resto == 11:
        return "0"

    if resto == 10:
        return "K"

    return str(resto)


def formatear_rut(rut):
    """Formato de almacenamiento: 12345678-5."""
    limpio = limpiar_rut(rut)
    return f"{limpio[:-1]}-{limpio[-1]}"

def validar_rut(valor):
    """Valida el formato y dígito verificador del RUT."""
    limpio = limpiar_rut(valor)
    if not re.fullmatch(r"\d{7,8}[\dK]", limpio):
        raise ValidationError(
            "El RUT no tiene un formato válido. Ejemplo: 12.345.678-5"
        )
    if calcular_dv(limpio[:-1]) != limpio[-1]:
        raise ValidationError(
            "El RUT no es válido: el dígito verificador no coincide."
        )

#============================== VALIDACIÓN TELÉFONO ==============================
# Requiere: pip install phonenumbers

# País que se asume cuando el número se escribe sin código internacional (sin "+").
REGION_TELEFONO_POR_DEFECTO = "CL"


def limpiar_telefono(telefono):
    """Deja solo los números y conserva el + inicial si existe."""
    telefono = telefono.strip()

    if telefono.startswith("+"):
        return "+" + re.sub(r"\D", "", telefono[1:])

    return re.sub(r"\D", "", telefono)


def _parsear_telefono(valor, region):
    """Interpreta el número; devuelve None si no se puede leer."""
    try:
        return phonenumbers.parse(valor.strip(), region)
    except phonenumbers.NumberParseException:
        return None


def formatear_telefono(telefono, region=REGION_TELEFONO_POR_DEFECTO):
    """Normaliza el teléfono al formato internacional E.164: +56912345678.

    `region` es el código ISO del país (ej. "CL") que se asume si el número no
    trae "+". Si el número no es válido, devuelve solo los dígitos limpios.
    """
    numero = _parsear_telefono(telefono, region)
    if numero is not None and phonenumbers.is_valid_number(numero):
        return phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164)
    return limpiar_telefono(telefono)


def validar_telefono(valor, region=REGION_TELEFONO_POR_DEFECTO):
    """Valida un teléfono de cualquier país (fijo o móvil) según su plan de numeración."""
    numero = _parsear_telefono(valor, region)
    if numero is None or not phonenumbers.is_valid_number(numero):
        raise ValidationError(
            "El teléfono no tiene un formato válido. "
            "Ejemplo: +56 9 1234 5678"
        )

#============================== VALIDACIÓN CORREO ==============================

def validar_email(valor):
    """Valida que el correo electrónico tenga un formato válido."""
    valor = valor.strip()
    if not valor:
        raise ValidationError(
            "Debes ingresar un correo electrónico."
        )
    try:
        validate_email(valor)
    except ValidationError:
        raise ValidationError(
            "El correo electrónico no tiene un formato válido."
        )
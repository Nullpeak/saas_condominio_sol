import re

from django.core.exceptions import ValidationError


def limpiar_rut(rut):
    """Deja solo dígitos y K en mayúscula: '12.345.678-5' -> '123456785'."""
    return re.sub(r"[.\s-]", "", rut).upper()


def calcular_dv(cuerpo):
    """Dígito verificador por módulo 11."""
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
    """Formato de almacenamiento: '12345678-5'."""
    limpio = limpiar_rut(rut)
    return f"{limpio[:-1]}-{limpio[-1]}"


def validar_rut(valor):
    limpio = limpiar_rut(valor)
    if not re.fullmatch(r"\d{7,8}[\dK]", limpio):
        raise ValidationError("El RUT no tiene un formato válido. Ejemplo: 12.345.678-5")
    if calcular_dv(limpio[:-1]) != limpio[-1]:
        raise ValidationError("El RUT no es válido: el dígito verificador no coincide.")
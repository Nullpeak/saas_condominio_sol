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


# Los RUT reales parten en 1.000.000. Bajo eso solo hay valores de relleno como 0.000.000-0,
# que igual "cuadran" con el módulo 11 (cuerpo 0 -> dígito verificador 0).
RUT_MINIMO = 1_000_000


def formatear_rut(rut):
    """Formato de almacenamiento: 12345678-5 (sin ceros a la izquierda)."""
    limpio = limpiar_rut(rut)
    cuerpo = limpio[:-1].lstrip("0") or "0"
    return f"{cuerpo}-{limpio[-1]}"

def validar_rut(valor):
    """Valida formato, rango y dígito verificador del RUT."""
    limpio = limpiar_rut(valor)
    if not re.fullmatch(r"\d{7,8}[\dK]", limpio):
        raise ValidationError(
            "El RUT no tiene un formato válido. Ejemplo: 12.345.678-5"
        )
    cuerpo = limpio[:-1]
    # Cuerpo 0 o muy bajo (0.000.000-0, 0.000.001-9...) o un solo dígito repetido
    # (11.111.111-1, 22.222.222-2...): son RUT de relleno, no de personas.
    if int(cuerpo) < RUT_MINIMO or len(set(cuerpo.lstrip("0"))) == 1:
        raise ValidationError("El RUT no es válido.")
    if calcular_dv(cuerpo) != limpio[-1]:
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

#============================== NORMALIZACIÓN DE TEXTO ==============================

# Partículas de apellidos y nombres que van en minúscula salvo al inicio: "De la Fuente", "María del Carmen".
PARTICULAS_NOMBRE = {"de", "del", "la", "las", "los", "y", "e", "da", "das", "do", "dos", "di", "van", "von", "der"}


def normalizar_espacios(texto):
    """Quita espacios al inicio/fin y deja un solo espacio entre palabras."""
    return " ".join((texto or "").split())


def _capitalizar_palabra(palabra):
    # Respeta guiones y apóstrofos: "maría-josé" -> "María-José", "o'higgins" -> "O'Higgins"
    partes = re.split(r"([-'’])", palabra.lower())
    return "".join(p[:1].upper() + p[1:] if p not in ("-", "'", "’") else p for p in partes)


def normalizar_nombre_propio(texto):
    """
    Nombres y apellidos de personas: cada palabra con la primera letra en mayúscula.
    "jose luis  IGNACIO" -> "Jose Luis Ignacio"; "DE LA FUENTE" -> "De la Fuente".
    """
    palabras = normalizar_espacios(texto).split(" ")
    resultado = []
    for i, palabra in enumerate(palabras):
        if not palabra:
            continue
        if i > 0 and palabra.lower() in PARTICULAS_NOMBRE:
            resultado.append(palabra.lower())
        else:
            resultado.append(_capitalizar_palabra(palabra))
    return " ".join(resultado)


def normalizar_email(valor):
    """Correos sin espacios y en minúscula: así "Juan@Mail.cl" y "juan@mail.cl" son el mismo."""
    return (valor or "").strip().lower()


def normalizar_patente(valor):
    """Patente en mayúscula y sin espacios, puntos ni guiones: "ab-cd 12" -> "ABCD12"."""
    return re.sub(r"[\s.\-·]", "", valor or "").upper()


def normalizar_telefono_opcional(valor, region=REGION_TELEFONO_POR_DEFECTO):
    """
    Para campos de teléfono opcionales: vacío -> "", si no, lo valida y lo deja en +56912345678.
    Lanza ValidationError si no es un número válido.
    """
    valor = (valor or "").strip()
    if not valor:
        return ""
    validar_telefono(valor, region)
    return formatear_telefono(valor, region)

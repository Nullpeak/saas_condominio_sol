def cupo_ocupado(unidad, relacion):
    """
    ¿Está ocupado el cupo de "dueno" o "arrendatario" de la unidad?

    Un arrendatario inactivo (Usuario.activo = False) deja el cupo libre: se puede invitar a otro
    en su lugar. El dueño no tiene esta excepción.
    """
    ocupante = getattr(unidad, relacion)
    if ocupante is None:
        return False
    if relacion == "arrendatario" and not ocupante.activo:
        return False
    return True
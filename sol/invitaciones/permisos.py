from core.models import Usuario

Rol = Usuario.Rol

# Quién puede invitar a quién. Nadie puede invitar a un Super Administrador.
PUEDE_INVITAR = {
    Rol.SUPER: {Rol.ADMINISTRADOR, Rol.GESTOR, Rol.RESIDENTE},
    Rol.ADMINISTRADOR: {Rol.GESTOR, Rol.RESIDENTE},
    Rol.GESTOR: {Rol.RESIDENTE},
}
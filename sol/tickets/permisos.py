"""
Reglas de quién ve y quién hace qué con los tickets. Las vistas solo preguntan aquí.

- Super Administrador: ve y gestiona los tickets de todos los condominios.
- Administrador / Gestor: ve y gestiona los tickets de los condominios que tiene asignados
  (un administrador puede tener varios: Usuario.condominios_asignados).
- Residente: ve solo los tickets que él creó; puede comentar, confirmar la solución,
  reabrir un ticket resuelto, cerrar (cancelar) uno propio y editarlo mientras siga abierto.
"""
from django.db.models import Q

from core.models import Usuario
from .models import Ticket

Rol = Usuario.Rol
E = Ticket.Estado

ROLES_STAFF = (Rol.SUPER, Rol.ADMINISTRADOR, Rol.GESTOR)

# A qué estados puede mover el staff desde cada estado
TRANSICIONES_STAFF = {
    E.ABIERTO: {E.EN_REVISION, E.ESPERANDO, E.RESUELTO, E.CERRADO},
    E.EN_REVISION: {E.ESPERANDO, E.RESUELTO, E.CERRADO},
    E.ESPERANDO: {E.EN_REVISION, E.RESUELTO, E.CERRADO},
    E.RESUELTO: {E.EN_REVISION, E.CERRADO},
    E.CERRADO: {E.EN_REVISION},  # reabrir
}

# Acciones del residente sobre su propio ticket: nombre -> (desde, hacia)
ACCIONES_RESIDENTE = {
    "confirmar": ({E.RESUELTO}, E.CERRADO),  # "sí, quedó solucionado"
    "reabrir": ({E.RESUELTO}, E.EN_REVISION),  # "no se solucionó"
    "cancelar": ({E.ABIERTO, E.EN_REVISION, E.ESPERANDO}, E.CERRADO),  # ya no lo necesita
}


def es_staff(perfil):
    return perfil is not None and perfil.rol in ROLES_STAFF


def tickets_visibles(perfil):
    qs = Ticket.objects.all()
    if perfil.rol == Rol.SUPER:
        return qs
    if perfil.rol in ROLES_STAFF:
        return qs.filter(condominio__in=perfil.condominios_asignados())
    return qs.filter(usuario=perfil)


def puede_gestionar(perfil, ticket):
    """Cambiar estado, prioridad, categoría y asignación."""
    if perfil.rol == Rol.SUPER:
        return True
    return perfil.rol in ROLES_STAFF and perfil.puede_acceder_condominio(ticket.condominio_id)


def estados_siguientes(perfil, ticket):
    if not puede_gestionar(perfil, ticket):
        return set()
    return TRANSICIONES_STAFF.get(ticket.estado, set())


def acciones_residente(perfil, ticket):
    """Acciones disponibles para el creador del ticket (si no es staff con permisos de gestión)."""
    if ticket.usuario_id != perfil.pk or puede_gestionar(perfil, ticket):
        return []
    return [nombre for nombre, (desde, _) in ACCIONES_RESIDENTE.items() if ticket.estado in desde]


def puede_editar(perfil, ticket):
    """El creador corrige asunto, descripción o categoría mientras nadie lo haya tomado."""
    return (
        ticket.usuario_id == perfil.pk
        and ticket.estado == E.ABIERTO
        and not puede_gestionar(perfil, ticket)
    )


def puede_comentar(perfil, ticket):
    if puede_gestionar(perfil, ticket):
        return True
    # El residente puede escribir mientras no esté cerrado
    return ticket.usuario_id == perfil.pk and ticket.estado != E.CERRADO


def asignables(condominio_id):
    """Staff activo al que se le puede asignar un ticket de ese condominio."""
    return Usuario.objects.filter(
        Q(condominio_id=condominio_id, rol__in=(Rol.ADMINISTRADOR, Rol.GESTOR))
        | Q(condominios_administrados=condominio_id, rol=Rol.ADMINISTRADOR)
        | Q(rol=Rol.SUPER),
        activo=True,
        user__isnull=False,
    ).distinct().order_by("nombre", "apellido")

"""
Reglas de quién ve y quién hace qué con los tickets. Las vistas solo preguntan aquí.

- Super Administrador: ve y gestiona los tickets de todos los condominios.
- Administrador / Gestor: ve y gestiona los tickets de los condominios que tiene asignados
  (un administrador puede tener varios: Usuario.condominios_asignados).
- Residente: ve solo los tickets que él creó; puede comentar, reabrir un ticket resuelto
  y editarlo mientras siga abierto. Solo administración o el gestor asignado cierra/reabre.
"""
from django.db.models import Q

from core.models import Usuario
from .models import Ticket

Rol = Usuario.Rol
E = Ticket.Estado

ROLES_STAFF = (Rol.SUPER, Rol.ADMINISTRADOR, Rol.GESTOR)

# A qué estados puede mover el staff desde cada estado
TRANSICIONES_STAFF = {
    E.ABIERTO: {E.EN_REVISION, E.ESPERANDO, E.RESUELTO},
    E.EN_REVISION: {E.ESPERANDO, E.RESUELTO},
    E.ESPERANDO: {E.EN_REVISION, E.RESUELTO},
    E.RESUELTO: {E.EN_REVISION},
    E.CERRADO: set(),
}

# Acciones del residente sobre su propio ticket: nombre -> (desde, hacia)
ACCIONES_RESIDENTE = {
    "reabrir": ({E.RESUELTO}, E.EN_REVISION),  # "no se solucionó"
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


def puede_cerrar_reabrir(perfil, ticket):
    """Cerrar o reabrir tickets: admin del condominio o gestor asignado."""
    if perfil.rol == Rol.SUPER:
        return True
    if not perfil.puede_acceder_condominio(ticket.condominio_id):
        return False
    if perfil.rol == Rol.ADMINISTRADOR:
        return True
    return perfil.rol == Rol.GESTOR and ticket.asignado_id == perfil.pk


def estados_siguientes(perfil, ticket):
    if not puede_gestionar(perfil, ticket):
        return set()
    siguientes = set(TRANSICIONES_STAFF.get(ticket.estado, set()))
    if ticket.estado == E.CERRADO:
        return {E.EN_REVISION} if puede_cerrar_reabrir(perfil, ticket) else set()
    if puede_cerrar_reabrir(perfil, ticket):
        siguientes.add(E.CERRADO)
    return siguientes


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
    if ticket.estado == E.CERRADO:
        return False
    if puede_gestionar(perfil, ticket):
        return True
    return ticket.usuario_id == perfil.pk


def asignables(condominio_id):
    """Staff activo al que se le puede asignar un ticket de ese condominio."""
    return Usuario.objects.filter(
        Q(condominio_id=condominio_id, rol__in=(Rol.ADMINISTRADOR, Rol.GESTOR))
        | Q(condominios_administrados=condominio_id, rol=Rol.ADMINISTRADOR)
        | Q(rol=Rol.SUPER),
        activo=True,
        user__isnull=False,
    ).distinct().order_by("nombre", "apellido")

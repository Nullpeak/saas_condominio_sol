# Progreso del proyecto - SaaS Condominio

## Estado actual

Fecha: 07/10/2026
Rama: feature/HU-010-recuperar-contrasena

## Tecnologías

- Python 3.11.9
- Django 5.2.17
- Django REST Framework
- SQLite durante desarrollo
- MySQL previsto para implementación final
- React previsto para frontend
- Git / GitHub

## Funcionalidades completadas

### HU-001 - Registrar y configurar condominio
- Creación de condominios.
- Selección de ciudad.
- Configuración de cantidad de edificios.
- Generación automática de edificios.

### HU-002 - Gestionar edificios
- Visualización de edificios.
- Edición de edificios.
- Configuración de pisos.
- Configuración de viviendas por piso.
- Generación automática de unidades.

### HU-003 - Gestionar unidades habitacionales
- Visualización de unidades.
- Asociación de propietario.
- Asociación de arrendatario.
- Edición de unidades.

### HU-006 - Gestionar usuarios
- Creación de usuarios.
- Edición de usuarios.
- Asociación a condominio.
- Asignación de roles.
- Activación y desactivación.
- Protección para evitar la desactivación del propio Super Administrador.

### HU-007 - Roles y permisos
Roles implementados:
- Super Administrador
- Administrador
- Gestor
- Residente

Se implementaron restricciones de acceso según el rol.

### HU-009 - Inicio de sesión
- Inicio de sesión.
- Cierre de sesión.
- Redirección al sistema.
- Dashboard según rol.

### HU-010 - Recuperar contraseña
- Solicitud de recuperación.
- Envío mediante el sistema de Django.
- Confirmación de nueva contraseña.
- Pantallas correspondientes implementadas.

## Validaciones implementadas

### RUT
- Validación de formato.
- Cálculo del dígito verificador mediante módulo 11.
- Normalización al formato 12345678-5.
- Comprobación de RUT duplicado.

### Teléfono
- Validación de números móviles chilenos.
- Acepta formatos como:
  - 912345678
  - 56912345678
  - +56912345678
  - +56 9 1234 5678
- Normalización al formato +56912345678.

## Interfaz

Se implementó una interfaz SaaS común con:
- ase.html
- Sidebar.
- Barra superior.
- Dashboard.
- Navegación según rol.
- Diseño responsive.
- Landing page pública.
- Pantallas actualizadas de condominios, edificios, unidades y usuarios.

## Git

Último commit:

deceb9b - Validaciones de usuarios y mejoras de interfaz

La rama está sincronizada con GitHub:

eature/HU-010-recuperar-contrasena

La carpeta media/ está excluida mediante .gitignore.

## Próximo trabajo

1. Implementar validación de correo electrónico.
2. Probar creación y edición de usuarios con la nueva validación.
3. Continuar con las funcionalidades restantes del Product Backlog.
4. Mantener actualizado este archivo al alcanzar nuevos hitos.

## Último punto alcanzado

Las validaciones de RUT y teléfono fueron implementadas y probadas correctamente.

El siguiente paso acordado es implementar la validación del correo electrónico.

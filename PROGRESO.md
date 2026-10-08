# Progreso del proyecto - SaaS Condominio

## Estado actual

Fecha: 07/10/2026

Rama: feature/sprint-1

## Tecnologías

* Python 3.11.9
* Django 5.2.17
* Django REST Framework
* SQLite durante desarrollo
* MySQL previsto para implementación final
* React previsto para frontend
* Git / GitHub

## Funcionalidades completadas

### HU-001 - Registrar y configurar condominio

* Creación de condominios.
* Selección de ciudad.
* Configuración de cantidad de edificios.
* Generación automática de edificios.

### HU-002 - Gestionar edificios

* Visualización de edificios.
* Edición de edificios.
* Configuración de pisos.
* Configuración de viviendas por piso.
* Generación automática de unidades.

### HU-003 - Gestionar unidades habitacionales

* Visualización de unidades.
* Asociación de propietario.
* Asociación de arrendatario.
* Edición de unidades.

### HU-006 - Gestionar usuarios

* Creación de usuarios.
* Edición de usuarios.
* Asociación a condominio.
* Asignación de roles.
* Activación y desactivación.
* Protección para evitar la desactivación de la propia cuenta.

### HU-007 - Roles y permisos

Roles implementados:

* Super Administrador
* Administrador
* Gestor
* Residente

Se implementaron restricciones de acceso según el rol.

### HU-009 - Inicio de sesión

* Inicio de sesión.
* Cierre de sesión.
* Redirección al sistema.
* Redirección a la Landing pública al cerrar sesión.
* Dashboard según rol.

### HU-010 - Recuperar contraseña

* Solicitud de recuperación.
* Envío mediante el sistema de Django.
* Confirmación de nueva contraseña.
* Pantallas correspondientes implementadas.
* Eliminación de mensajes de depuración relacionados con los tokens de recuperación.

## Validaciones implementadas

### RUT

* Validación de formato.
* Cálculo del dígito verificador mediante módulo 11.
* Normalización al formato 12345678-5.
* Comprobación de RUT duplicado.

### Teléfono

* Validación de números móviles chilenos.
* Acepta formatos como:

  * 912345678
  * 56912345678
  * +56912345678
  * +56 9 1234 5678
* Normalización al formato +56912345678.

### Correo electrónico

* Validación de formato.
* Validación al crear usuarios.
* Validación al editar usuarios.
* Comprobación de correo duplicado.
* Normalización del correo.

## Interfaz

Se implementó una interfaz SaaS común con:

* base.html
* Sidebar.
* Barra superior.
* Dashboard.
* Navegación según rol.
* Diseño responsive.
* Landing page pública.
* Pantallas actualizadas de condominios, edificios, unidades y usuarios.

## Git

La rama está sincronizada con GitHub:

feature/HU-010-recuperar-contrasena

La carpeta media/ está excluida mediante .gitignore.

## Próximo trabajo

1. Continuar con las funcionalidades restantes del Product Backlog.
2. Implementar y probar las siguientes historias de usuario.
3. Mantener actualizado este archivo al alcanzar nuevos hitos.

## Último punto alcanzado

Se implementaron y probaron correctamente las validaciones de RUT, teléfono y correo electrónico.

También se incorporó protección para evitar la desactivación de la propia cuenta y se modificó el cierre de sesión para redirigir a la Landing pública.

La funcionalidad de recuperación de contraseña se encuentra implementada y probada.

El proyecto queda listo para continuar con las siguientes funcionalidades del Product Backlog.

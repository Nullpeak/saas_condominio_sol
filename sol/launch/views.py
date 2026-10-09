from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from core.models import Ciudad, Condominio, Usuario

from .forms import CondominioForm, SuperUsuarioForm
from .guards import setup_disponible, solo_primer_lanzamiento
from .paises import ciudades_de


@require_http_methods(["GET", "POST"])
@solo_primer_lanzamiento
def launch(request):
    if request.method == "POST":
        user_form = SuperUsuarioForm(request.POST, prefix="u")
        condo_form = CondominioForm(request.POST, request.FILES, prefix="c")

        if user_form.is_valid() and condo_form.is_valid():
            u, c = user_form.cleaned_data, condo_form.cleaned_data

            with transaction.atomic():
                # Se vuelve a comprobar dentro de la transacción: si dos personas
                # envían el formulario a la vez, solo una debería terminar creando.
                if not setup_disponible():
                    raise Http404

                # Se carga en la tabla Ciudad todas las ciudades del país elegido
                # y se usa la que escogió el usuario para el condominio.
                Ciudad.objects.bulk_create(
                    [Ciudad(nombre=n) for n in ciudades_de(c["pais"])],
                    ignore_conflicts=True,
                )
                ciudad = Ciudad.objects.get(nombre=c["ciudad"])
                condominio = Condominio.objects.create(
                    nombre=c["nombre"],
                    direccion=c["direccion"],
                    ciudad=ciudad,
                    edificios=c["edificios"],  # Condominio.save() crea los Edificio
                    portada=c["portada"] or "",
                )
                # Condominio.save() ya creó los edificios. Se les asignan pisos y viviendas
                # por piso y se guardan: Edificio.save() genera sus unidades solo.
                for edificio in condominio.edificios_set.all():
                    edificio.numero_pisos = c["numero_pisos"]
                    edificio.viviendas_por_piso = c["viviendas_por_piso"]
                    edificio.save()
                auth_user = get_user_model().objects.create_superuser(
                    username=u["username"],
                    email=u["email"],
                    password=u["password1"],
                )
                Usuario.objects.create(
                    user=auth_user,
                    condominio=condominio,
                    nombre=u["nombre"],
                    nombre2=u["nombre2"],
                    apellido=u["apellido"],
                    apellido2=u["apellido2"],
                    email=u["email"],
                    rut=u["rut"],
                    telefono=u["telefono"],
                    rol=Usuario.Rol.SUPER,
                )

            login(request, auth_user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect(getattr(settings, "LOGIN_REDIRECT_URL", "/"))
    else:
        user_form = SuperUsuarioForm(prefix="u")
        condo_form = CondominioForm(prefix="c")

    return render(request, "launch/launch.html", {"user_form": user_form, "condo_form": condo_form})


@require_GET
@solo_primer_lanzamiento
def ciudades(request):
    """Ciudades de un país, para llenar el selector del formulario."""
    return JsonResponse({"ciudades": list(ciudades_de(request.GET.get("pais")))})
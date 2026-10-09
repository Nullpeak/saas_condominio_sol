"""Países y ciudades para el setup inicial.

Usa la librería `geonamescache` (pip install geonamescache): trae los datos de
GeoNames dentro del paquete, así que funciona sin internet. Incluye las ciudades
de más de 15.000 habitantes.
"""
from functools import lru_cache

from geonamescache import GeonamesCache

MAX_NOMBRE = 50  # largo máximo de Ciudad.nombre


@lru_cache(maxsize=1)
def _gc():
    return GeonamesCache()


@lru_cache(maxsize=1)
def listar_paises():
    """Lista de (codigo_iso, nombre) ordenada por nombre."""
    paises = _gc().get_countries()
    return tuple(sorted(((iso, d["name"]) for iso, d in paises.items()), key=lambda p: p[1]))


@lru_cache(maxsize=None)
def ciudades_de(iso):
    """Nombres de ciudades del país (sin repetidos, ordenados)."""
    iso = (iso or "").upper()
    nombres = {
        c["name"]
        for c in _gc().get_cities().values()
        if c["countrycode"] == iso and len(c["name"]) <= MAX_NOMBRE
    }
    return tuple(sorted(nombres))

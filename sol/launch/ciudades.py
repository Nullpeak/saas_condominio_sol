"""Ciudades de Chile para poblar la tabla Ciudad en el setup inicial.

Usa la librería `geonamescache` (pip install geonamescache): trae los datos de
GeoNames dentro del paquete, así que funciona sin internet. Incluye las ciudades
de más de 15.000 habitantes.
"""
from functools import lru_cache

from geonamescache import GeonamesCache

MAX_NOMBRE = 50  # largo máximo de Ciudad.nombre


@lru_cache(maxsize=1)
def ciudades_chile():
    """Nombres de las ciudades de Chile (sin repetidos, ordenados)."""
    nombres = {
        c["name"]
        for c in GeonamesCache().get_cities().values()
        if c["countrycode"] == "CL" and len(c["name"]) <= MAX_NOMBRE
    }
    return tuple(sorted(nombres))
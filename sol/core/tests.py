from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from .validators import formatear_rut, validar_rut


class RutTests(SimpleTestCase):

    def test_validos(self):
        for rut in ["12.345.678-5", "12345678-5", "10.880.932-9", "21022295-2", "1.000.005-K", "1000005-k"]:
            validar_rut(rut)  # no debe lanzar error

    def test_invalidos(self):
        casos = [
            "0.000.000-0", "00000000-0", "0-0", "000.000-0",  # cuerpo cero
            "0.000.001-9", "999.999-3",                        # bajo el mínimo
            "11.111.111-1", "22.222.222-2", "1.111.111-4",     # dígito repetido
            "12.345.678-9",                                    # verificador incorrecto
            "", "abc", "12.345.678",                           # formato
        ]
        for rut in casos:
            with self.subTest(rut=rut), self.assertRaises(ValidationError):
                validar_rut(rut)

    def test_formato(self):
        self.assertEqual(formatear_rut("12.345.678-5"), "12345678-5")
        self.assertEqual(formatear_rut("01.234.567-k"), "1234567-K")

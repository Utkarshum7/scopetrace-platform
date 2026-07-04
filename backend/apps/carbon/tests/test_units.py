from decimal import Decimal

from django.test import TestCase

from apps.carbon.models import UnitConversion
from apps.carbon.services.units import UnitConverter, UnitConversionError
from apps.carbon.tests import factories as f


class UnitConversionTests(TestCase):
    def setUp(self):
        f.unit_conversion("MWh", "kWh", "1000", UnitConversion.Dimension.ENERGY)
        f.unit_conversion("t", "kg", "1000", UnitConversion.Dimension.MASS)
        self.converter = UnitConverter()

    def test_identity(self):
        self.assertEqual(self.converter.convert("500", "L", "L"), Decimal("500"))

    def test_forward_conversion(self):
        self.assertEqual(self.converter.convert("2.5", "MWh", "kWh"), Decimal("2500.000"))

    def test_inverse_conversion(self):
        # kWh -> MWh uses the inverse of the declared MWh->kWh factor.
        self.assertEqual(self.converter.convert("2500", "kWh", "MWh"), Decimal("2.5"))

    def test_unknown_conversion_raises(self):
        with self.assertRaises(UnitConversionError):
            self.converter.convert("1", "L", "kWh")  # cross-dimension, undefined

    def test_decimal_no_float_artifacts(self):
        # 0.1 t -> 100 kg exactly (Decimal, not 99.99999...)
        self.assertEqual(self.converter.convert("0.1", "t", "kg"), Decimal("100.0"))

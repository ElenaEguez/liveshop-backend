from django.test import TestCase

from products.barcode_utils import (
    build_ean13_from12,
    ean13_check_digit,
    generate_ean13_candidate,
    is_valid_ean13,
)


class BarcodeUtilsTests(TestCase):
    def test_ean13_check_digit_known(self):
        self.assertEqual(ean13_check_digit('769277892440'), 9)
        self.assertTrue(is_valid_ean13('7692778924409'))

    def test_invalid_sku_not_ean13(self):
        self.assertFalse(is_valid_ean13('GINDB1-01'))
        self.assertFalse(is_valid_ean13(''))
        self.assertFalse(is_valid_ean13('123'))

    def test_generate_candidate_is_valid(self):
        code = generate_ean13_candidate()
        self.assertEqual(len(code), 13)
        self.assertTrue(is_valid_ean13(code))
        self.assertTrue(code.startswith('2'))

    def test_build_from_12(self):
        self.assertEqual(build_ean13_from12('769277892440'), '7692778924409')

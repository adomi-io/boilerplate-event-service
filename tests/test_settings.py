"""The environment helpers.

    python -m unittest discover -s tests -v
"""
import unittest
from unittest import mock

from src import settings


class TestEnv(unittest.TestCase):

    def test_unset_returns_the_default(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(settings.env('EXAMPLE_NAME', 'fallback'), 'fallback')

    def test_blank_counts_as_unset(self):
        with mock.patch.dict('os.environ', {'EXAMPLE_NAME': '   '}):
            self.assertEqual(settings.env('EXAMPLE_NAME', 'fallback'), 'fallback')

    def test_value_is_stripped(self):
        with mock.patch.dict('os.environ', {'EXAMPLE_NAME': '  value '}):
            self.assertEqual(settings.env('EXAMPLE_NAME'), 'value')

    def test_env_int(self):
        with mock.patch.dict('os.environ', {'EXAMPLE_PORT': '5672'}):
            self.assertEqual(settings.env_int('EXAMPLE_PORT', 1), 5672)

        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertEqual(settings.env_int('EXAMPLE_PORT', 1), 1)

    def test_env_bool(self):
        for value in settings.TRUE_VALUES:
            with mock.patch.dict('os.environ', {'EXAMPLE_FLAG': value.upper()}):
                self.assertTrue(settings.env_bool('EXAMPLE_FLAG'))

        with mock.patch.dict('os.environ', {'EXAMPLE_FLAG': 'no'}):
            self.assertFalse(settings.env_bool('EXAMPLE_FLAG', True))

        with mock.patch.dict('os.environ', {}, clear=True):
            self.assertTrue(settings.env_bool('EXAMPLE_FLAG', True))


if __name__ == '__main__':
    unittest.main(verbosity=2)

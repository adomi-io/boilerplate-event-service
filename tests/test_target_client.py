"""The outside-API client wiring: headers, error classification, parsed bodies.

TEMPLATE: keep these and add one test per call you write.

    python -m unittest discover -s tests -v
"""
import unittest
from unittest import mock

import requests

from src.worker.target_client import TargetClient, TargetError


class FakeResponse:

    def __init__(self, status_code, text='', body=None):
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self._body = body

    def json(self):
        return self._body


class TestRequest(unittest.TestCase):

    def setUp(self):
        self.client = TargetClient(base_url='http://target.test/', api_key='secret', timeout=5)

    def test_sends_json_with_the_key_and_returns_the_body(self):
        response = FakeResponse(200, '{"ok": true}', {'ok': True})

        with mock.patch('src.worker.target_client.requests.request', return_value=response) as request:
            result = self.client._request('POST', '/examples', payload={'id': 'x'})

        self.assertEqual(result, {'ok': True})
        request.assert_called_once_with(
            'POST',
            'http://target.test/examples',
            json={'id': 'x'},
            headers={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer secret',
            },
            timeout=5,
        )

    def test_empty_body_is_none(self):
        with mock.patch('src.worker.target_client.requests.request', return_value=FakeResponse(204)):
            self.assertIsNone(self.client._request('DELETE', '/examples/x'))

    def test_error_status_raises_with_the_status(self):
        with mock.patch('src.worker.target_client.requests.request', return_value=FakeResponse(500, 'boom')):
            with self.assertRaises(TargetError) as raised:
                self.client._request('GET', '/examples')

        self.assertEqual(raised.exception.status, 500)
        self.assertIn('boom', str(raised.exception))

    def test_unreachable_raises_with_status_zero(self):
        with mock.patch('src.worker.target_client.requests.request', side_effect=requests.ConnectionError('refused')):
            with self.assertRaises(TargetError) as raised:
                self.client._request('GET', '/examples')

        self.assertEqual(raised.exception.status, 0)

    def test_unset_url_raises_before_any_call(self):
        client = TargetClient(base_url=None)

        with mock.patch('src.worker.target_client.requests.request') as request:
            with self.assertRaises(TargetError):
                client._request('GET', '/examples')

        request.assert_not_called()

    def test_no_key_means_no_authorization_header(self):
        client = TargetClient(base_url='http://target.test', api_key=None)

        self.assertNotIn('Authorization', client._headers())


class TestRetryable(unittest.TestCase):

    def test_outages_and_rate_limits_are_retryable(self):
        for status in (0, 408, 429, 500, 503):
            self.assertTrue(TargetError('x', status=status).retryable, status)

    def test_other_client_errors_are_not(self):
        for status in (400, 401, 404, 422):
            self.assertFalse(TargetError('x', status=status).retryable, status)


if __name__ == '__main__':
    unittest.main(verbosity=2)

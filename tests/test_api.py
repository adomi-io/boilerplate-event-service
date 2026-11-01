"""The Flask api with the broker mocked: every route, and the envelope /action hands to the publisher.

    python -m unittest discover -s tests -v
"""
import json
import unittest
from unittest import mock

from src.api import main as api
from src.contracts import ASYNCAPI_DOCUMENT, EVENT_SCHEMAS, OPENAPI_DOCUMENT

# the first event in the contract and its request example
EVENT_TYPE = sorted(EVENT_SCHEMAS)[0]
EXAMPLE = OPENAPI_DOCUMENT['paths']['/action']['post']['requestBody']['content']['application/json']['examples'][EVENT_TYPE]['value']['data']


class ApiTestCase(unittest.TestCase):

    def setUp(self):
        api.app.config['TESTING'] = True
        self.client = api.app.test_client()

    def post(self, body, headers=None):
        return self.client.post(
            '/action',
            data=json.dumps(body),
            content_type='application/json',
            headers=headers or {},
        )


class TestReadOnlyRoutes(ApiTestCase):
    def test_health_check(self):
        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'status': 'ok'})

    def test_openapi_document(self):
        response = self.client.get('/openapi.json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), OPENAPI_DOCUMENT)

    def test_asyncapi_document(self):
        response = self.client.get('/asyncapi.json')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), ASYNCAPI_DOCUMENT)

    def test_contract_listing(self):
        response = self.client.get('/contracts')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sorted(response.get_json()), sorted(EVENT_SCHEMAS))

    def test_single_contract(self):
        response = self.client.get(f'/contracts/{EVENT_TYPE}')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), EVENT_SCHEMAS[EVENT_TYPE])

    def test_unknown_contract_is_404(self):
        response = self.client.get('/contracts/nope.never')

        self.assertEqual(response.status_code, 404)
        self.assertIn('error', response.get_json())


class TestAction(ApiTestCase):
    def test_missing_type_is_400(self):
        response = self.post({'data': EXAMPLE})

        self.assertEqual(response.status_code, 400)
        self.assertIn('type', response.get_json()['error'])

    def test_missing_data_is_400(self):
        response = self.post({'type': EVENT_TYPE})

        self.assertEqual(response.status_code, 400)
        self.assertIn('data', response.get_json()['error'])

    def test_non_object_body_is_400(self):
        response = self.post(['not', 'an', 'envelope'])

        self.assertEqual(response.status_code, 400)

    def test_untracked_type_is_accepted_but_not_published(self):
        with mock.patch.object(api.broker, 'publish_event') as publish:
            response = self.post({
                'type': 'some.other.event',
                'data': {},
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()['tracked_events'], sorted(EVENT_SCHEMAS))
        publish.assert_not_called()

    def test_invalid_payload_is_422_with_details(self):
        with mock.patch.object(api.broker, 'publish_event') as publish:
            response = self.post({
                'type': EVENT_TYPE,
                'data': 'not an object',
            })

        self.assertEqual(response.status_code, 422)

        body = response.get_json()

        self.assertIn(EVENT_TYPE, body['error'])
        self.assertEqual(body['contract'], f'/contracts/{EVENT_TYPE}')
        self.assertTrue(body['details'])
        publish.assert_not_called()

    def test_valid_event_is_published_and_202(self):
        with mock.patch.object(api.broker, 'publish_event') as publish:
            response = self.post({
                'type': EVENT_TYPE,
                'data': EXAMPLE,
            })

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.get_json(), {
            'queued': True,
            'type': EVENT_TYPE,
        })
        publish.assert_called_once_with(EVENT_TYPE, EXAMPLE)

    def test_broker_failure_is_503(self):
        with mock.patch.object(api.broker, 'publish_event', side_effect=RuntimeError('down')):
            response = self.post({
                'type': EVENT_TYPE,
                'data': EXAMPLE,
            })

        self.assertEqual(response.status_code, 503)
        self.assertIn('broker', response.get_json()['error'].lower())


class TestApiKey(ApiTestCase):
    def test_no_key_configured_means_open(self):
        with mock.patch.object(api, 'SERVICE_API_KEY', None):
            with mock.patch.object(api.broker, 'publish_event'):
                response = self.post({
                    'type': EVENT_TYPE,
                    'data': EXAMPLE,
                })

        self.assertEqual(response.status_code, 202)

    def test_missing_or_wrong_key_is_401(self):
        with mock.patch.object(api, 'SERVICE_API_KEY', 'secret'):
            with mock.patch.object(api.broker, 'publish_event') as publish:
                missing = self.post({'type': EVENT_TYPE, 'data': EXAMPLE})
                wrong = self.post({'type': EVENT_TYPE, 'data': EXAMPLE}, {'X-API-Key': 'nope'})

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        publish.assert_not_called()

    def test_right_key_passes(self):
        with mock.patch.object(api, 'SERVICE_API_KEY', 'secret'):
            with mock.patch.object(api.broker, 'publish_event'):
                response = self.post({'type': EVENT_TYPE, 'data': EXAMPLE}, {'X-API-Key': 'secret'})

        self.assertEqual(response.status_code, 202)


if __name__ == '__main__':
    unittest.main(verbosity=2)

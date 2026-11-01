"""The contract wiring: the OpenAPI document, the derived AsyncAPI document and validation. These
tests are generic; add an event and they check it without changes.

    python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import pathlib
import tempfile
import unittest

from src.contracts import ASYNCAPI_DOCUMENT, EVENT_SCHEMAS, OPENAPI_DOCUMENT, validate_event
from src.contracts.__main__ import main as export_contracts

EVENT_TYPES = sorted(OPENAPI_DOCUMENT['x-event-schemas'])
ENVELOPE = OPENAPI_DOCUMENT['components']['schemas']['EventEnvelope']


def find_refs(node):
    if isinstance(node, dict):
        if '$ref' in node:
            yield node['$ref']

        for value in node.values():
            yield from find_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from find_refs(value)


def request_examples():
    return OPENAPI_DOCUMENT['paths']['/action']['post']['requestBody']['content']['application/json']['examples']


class TestDocuments(unittest.TestCase):
    def test_at_least_one_event_is_tracked(self):
        self.assertTrue(EVENT_TYPES)

    def test_every_event_type_has_a_schema(self):
        self.assertEqual(sorted(EVENT_SCHEMAS), EVENT_TYPES)

    def test_inlined_schemas_have_no_refs(self):
        for event_type, schema in EVENT_SCHEMAS.items():
            self.assertEqual(list(find_refs(schema)), [], f'{event_type} still contains $refs')

    def test_envelope_discriminator_covers_every_event(self):
        self.assertEqual(sorted(ENVELOPE['discriminator']['mapping']), EVENT_TYPES)
        self.assertEqual(len(ENVELOPE['oneOf']), len(EVENT_TYPES))

    def test_every_envelope_pins_its_type(self):
        for event_type, ref in ENVELOPE['discriminator']['mapping'].items():
            schema = OPENAPI_DOCUMENT['components']['schemas'][ref.rsplit('/', 1)[1]]

            self.assertEqual(schema['properties']['type']['const'], event_type)
            self.assertEqual(schema['properties']['data']['$ref'], OPENAPI_DOCUMENT['x-event-schemas'][event_type])

    def test_asyncapi_has_a_channel_per_event(self):
        self.assertEqual(sorted(ASYNCAPI_DOCUMENT['channels']), EVENT_TYPES)

        for event_type, channel in ASYNCAPI_DOCUMENT['channels'].items():
            self.assertEqual(channel['address'], event_type)
            self.assertIn(event_type, channel['messages'])
            self.assertIn('amqp', channel['bindings'])

    def test_asyncapi_version_matches_openapi(self):
        self.assertEqual(ASYNCAPI_DOCUMENT['info']['version'], OPENAPI_DOCUMENT['info']['version'])


class TestRequestExamples(unittest.TestCase):
    def test_every_event_type_has_an_example(self):
        self.assertEqual(sorted(request_examples()), EVENT_TYPES)

    def test_examples_validate_against_their_own_contract(self):
        for name, example in request_examples().items():
            envelope = example['value']

            self.assertEqual(envelope['type'], name)
            self.assertEqual(validate_event(envelope['type'], envelope['data']), [], f'example {name} is off contract')


class TestValidation(unittest.TestCase):
    def test_missing_required_field_is_reported(self):
        for event_type, schema in EVENT_SCHEMAS.items():
            for field in schema.get('required', []):
                example = dict(request_examples()[event_type]['value']['data'])
                example.pop(field, None)

                errors = validate_event(event_type, example)

                self.assertTrue(any(field in error for error in errors), f'{event_type} without {field} passed')

    def test_wrong_shape_is_reported(self):
        for event_type in EVENT_TYPES:
            self.assertTrue(validate_event(event_type, 'not an object'))

    def test_unknown_event_type_raises(self):
        with self.assertRaises(KeyError):
            validate_event('nope.never', {})


class TestExporter(unittest.TestCase):
    def test_exports_all_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                export_contracts(['src.contracts', tmp])

            written = sorted(path.name for path in pathlib.Path(tmp).iterdir())

            self.assertEqual(written, ['asyncapi.json', 'events.json', 'openapi.json'])

            exported = json.loads((pathlib.Path(tmp) / 'openapi.json').read_text())

            self.assertEqual(exported, OPENAPI_DOCUMENT)


if __name__ == '__main__':
    unittest.main(verbosity=2)

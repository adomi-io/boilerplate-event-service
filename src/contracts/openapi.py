"""Loads openapi.yaml and builds a validator per event from its `x-event-schemas` mapping. OpenAPI
3.1 schemas are JSON Schema 2020-12, so the same components validate at the api and in the worker."""
import copy
import pathlib

import jsonschema
import yaml
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

OPENAPI_PATH = pathlib.Path(__file__).parent / 'openapi.yaml'
DOCUMENT_URI = 'urn:event-service:openapi'
MAX_REF_DEPTH = 32

with open(OPENAPI_PATH) as f:
    OPENAPI_DOCUMENT = yaml.safe_load(f)

_registry = Registry().with_resource(
    DOCUMENT_URI,
    Resource(contents=OPENAPI_DOCUMENT, specification=DRAFT202012),
)


# ===========================================
# Reference resolution
# ===========================================
def _lookup(ref):
    """The node an internal '#/components/...' pointer names."""
    node = OPENAPI_DOCUMENT

    for part in ref.lstrip('#/').split('/'):
        node = node[part.replace('~1', '/').replace('~0', '~')]

    return node


def _inline_refs(node, depth=0):
    """A copy of a schema node with every internal $ref replaced by its content."""
    if depth > MAX_REF_DEPTH:
        raise ValueError('$ref nesting too deep: cyclic contract schema?')

    if isinstance(node, dict):
        if isinstance(node.get('$ref'), str) and node['$ref'].startswith('#/'):
            resolved = _inline_refs(_lookup(node['$ref']), depth + 1)
            siblings = {key: value for key, value in node.items() if key != '$ref'}

            return {**copy.deepcopy(resolved), **_inline_refs(siblings, depth + 1)}

        return {key: _inline_refs(value, depth) for key, value in node.items()}

    if isinstance(node, list):
        return [_inline_refs(value, depth) for value in node]

    return node


def inlined_schema(ref):
    return _inline_refs(_lookup(ref))


# ===========================================
# Validators
# ===========================================
_VALIDATORS = {
    event_type: jsonschema.Draft202012Validator(
        {'$ref': f'{DOCUMENT_URI}{ref}'},
        registry=_registry,
    )
    for event_type, ref in OPENAPI_DOCUMENT['x-event-schemas'].items()
}

# event type to its self-contained payload schema, served at GET /contracts
EVENT_SCHEMAS = {
    event_type: inlined_schema(ref)
    for event_type, ref in OPENAPI_DOCUMENT['x-event-schemas'].items()
}


def validate_event(event_type, data):
    """The violations of the event's contract, empty when valid. KeyError for an unknown type."""
    validator = _VALIDATORS[event_type]

    return [
        f'{"/".join(str(part) for part in error.absolute_path) or "(root)"}: {error.message}'
        for error in validator.iter_errors(data)
    ]

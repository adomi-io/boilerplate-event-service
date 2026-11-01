"""The producer side (`api` mode).

    GET  /                   health check
    POST /action             validate one {type, data} envelope and publish it
    GET  /openapi.json       the OpenAPI document
    GET  /asyncapi.json      the AsyncAPI document of the bus
    GET  /contracts          every event payload schema, refs inlined
    GET  /contracts/<type>   one payload schema
"""
import hmac
import logging

from flask import Flask, jsonify, request

from src import broker
from src.contracts import ASYNCAPI_DOCUMENT, EVENT_SCHEMAS, OPENAPI_DOCUMENT, validate_event
from src.settings import env, setup_logging

setup_logging()

_logger = logging.getLogger(__name__)

# empty means /action is open
SERVICE_API_KEY = env('SERVICE_API_KEY')
HEADER_API_KEY = 'X-API-Key'

app = Flask(__name__)


# ===========================================
# Helpers
# ===========================================
def api_key_ok():
    if not SERVICE_API_KEY:
        return True

    provided = request.headers.get(HEADER_API_KEY, '')

    return hmac.compare_digest(provided.encode(), SERVICE_API_KEY.encode())


# ===========================================
# Health and contracts
# ===========================================
@app.get('/')
def health_check():
    return jsonify({
        'status': 'ok',
    })


@app.get('/openapi.json')
def get_openapi():
    return jsonify(OPENAPI_DOCUMENT)


@app.get('/asyncapi.json')
def get_asyncapi():
    return jsonify(ASYNCAPI_DOCUMENT)


@app.get('/contracts')
def list_contracts():
    return jsonify(EVENT_SCHEMAS)


@app.get('/contracts/<event_type>')
def get_contract(event_type):
    schema = EVENT_SCHEMAS.get(event_type)

    if schema is None:
        return jsonify({'error': f'Unknown event type: {event_type}'}), 404

    return jsonify(schema)


# ===========================================
# Events
# ===========================================
@app.post('/action')
def receive_event():
    if not api_key_ok():
        _logger.warning('rejected /action call with a missing or wrong %s', HEADER_API_KEY)
        return jsonify({'error': f'Invalid or missing {HEADER_API_KEY}'}), 401

    envelope = request.get_json(silent=True)

    if not isinstance(envelope, dict) or 'type' not in envelope:
        return jsonify({'error': 'Missing type field in JSON data'}), 400

    if 'data' not in envelope:
        return jsonify({'error': 'Missing data field in JSON data'}), 400

    event_type = envelope['type']
    data = envelope['data']

    # a producer may fan one stream out to several services
    if event_type not in EVENT_SCHEMAS:
        return jsonify({
            'message': f'Event {event_type} received but not published: not a tracked event',
            'tracked_events': sorted(EVENT_SCHEMAS),
        }), 200

    errors = validate_event(event_type, data)

    if errors:
        _logger.error('invalid %s payload: %s', event_type, '; '.join(errors))

        return jsonify({
            'error': f'Payload does not match the {event_type} contract',
            'details': errors,
            'contract': f'/contracts/{event_type}',
        }), 422

    try:
        broker.publish_event(event_type, data)
    except Exception as e:  # noqa: BLE001 - any broker failure is a 503 to the producer
        _logger.exception('failed to publish %s', event_type)

        return jsonify({'error': f'Message broker unavailable: {e}'}), 503

    _logger.info('queued %s', event_type)

    return jsonify({
        'queued': True,
        'type': event_type,
    }), 202


if __name__ == '__main__':
    app.run(host='0.0.0.0')

"""Derives the AsyncAPI 3.0 document of the bus from openapi.yaml: one channel per event type, the
envelope as the message payload, every schema copied in."""
from src.contracts.openapi import OPENAPI_DOCUMENT, inlined_schema
from src.settings import env

JSON_SCHEMA_FORMAT = 'application/schema+json;version=draft-2020-12'
AMQP_BINDING_VERSION = '0.3.0'

# TEMPLATE: the service's name
SERVICE_ID = 'urn:event-service'

EXCHANGE_NAME = env('RABBITMQ_EXCHANGE', 'events')

ENVELOPE_REFS = OPENAPI_DOCUMENT['components']['schemas']['EventEnvelope']['discriminator']['mapping']

AMQP_CHANNEL_BINDING = {
    'amqp': {
        'is': 'routingKey',
        'exchange': {
            'name': EXCHANGE_NAME,
            'type': 'topic',
            'durable': True,
            'autoDelete': False,
        },
        'bindingVersion': AMQP_BINDING_VERSION,
    },
}


def description():
    return (
        'The api publishes every validated envelope `{"type": <event type>, "data": <payload>}` to '
        'the `' + EXCHANGE_NAME + '` topic exchange with the event type as routing key, persistent, '
        'content type application/json. Bind your own durable queue to the exchange to consume; '
        'each queue gets its own copy of every message.'
    )


def _channel(event_type, payload_schema, envelope_schema):
    return {
        'address': event_type,
        'description': f'Routing key `{event_type}` on the `{EXCHANGE_NAME}` topic exchange.',
        'messages': {
            event_type: {
                'name': event_type,
                'title': payload_schema.get('title', event_type),
                'summary': payload_schema.get('description', ''),
                'contentType': 'application/json',
                'payload': {
                    'schemaFormat': JSON_SCHEMA_FORMAT,
                    'schema': envelope_schema,
                },
            },
        },
        'bindings': AMQP_CHANNEL_BINDING,
    }


def _operation(event_type):
    return {
        'action': 'send',
        'channel': {'$ref': f'#/channels/{event_type}'},
        'messages': [
            {'$ref': f'#/channels/{event_type}/messages/{event_type}'},
        ],
        'bindings': {
            'amqp': {
                'deliveryMode': 2,
                'bindingVersion': AMQP_BINDING_VERSION,
            },
        },
    }


def build_document():
    info = OPENAPI_DOCUMENT['info']

    channels = {}
    operations = {}

    for event_type, payload_ref in OPENAPI_DOCUMENT['x-event-schemas'].items():
        payload_schema = inlined_schema(payload_ref)
        envelope_schema = inlined_schema(ENVELOPE_REFS[event_type])

        # codegen tools name the root model from $id
        envelope_schema['$id'] = envelope_schema.get('title', event_type)

        channels[event_type] = _channel(event_type, payload_schema, envelope_schema)
        operations[f'send.{event_type}'] = _operation(event_type)

    return {
        'asyncapi': '3.0.0',
        'id': SERVICE_ID,
        'info': {
            'title': f'{info["title"]} Event Bus',
            'version': info['version'],
            'description': description(),
        },
        'defaultContentType': 'application/json',
        'servers': {
            'production': {
                'host': '{rabbitmqHost}',
                'protocol': 'amqps',
                'description': 'The RABBITMQ_* settings of the api and listener.',
                'variables': {
                    'rabbitmqHost': {
                        'description': 'RABBITMQ_HOST:RABBITMQ_PORT',
                    },
                },
            },
            'local': {
                'host': 'rabbitmq:5672',
                'protocol': 'amqp',
                'description': 'The docker compose RabbitMQ.',
            },
        },
        'channels': channels,
        'operations': operations,
    }


ASYNCAPI_DOCUMENT = build_document()

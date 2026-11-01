"""RabbitMQ wiring shared by the api and the listener: one durable topic exchange, the event type as
routing key, persistent JSON envelopes."""
import json
import logging
import socket
import ssl
import time

import pika

from src.settings import env, env_int

_logger = logging.getLogger(__name__)

RABBITMQ_SCHEME = env('RABBITMQ_SCHEME', 'amqps')
RABBITMQ_HOST = env('RABBITMQ_HOST', 'rabbitmq')
RABBITMQ_PORT = env_int('RABBITMQ_PORT', 5671 if RABBITMQ_SCHEME == 'amqps' else 5672)
RABBITMQ_USERNAME = env('RABBITMQ_USERNAME', 'guest')
RABBITMQ_PASSWORD = env('RABBITMQ_PASSWORD', 'guest')
RABBITMQ_VHOST = env('RABBITMQ_VHOST', '/')
RABBITMQ_EXCHANGE = env('RABBITMQ_EXCHANGE', 'events')
RABBITMQ_QUEUE = env('RABBITMQ_QUEUE', 'event_service_worker')
RABBITMQ_ROUTING_KEY = env('RABBITMQ_ROUTING_KEY', '#')
RABBITMQ_RETRY_QUEUE = env('RABBITMQ_RETRY_QUEUE', f'{RABBITMQ_QUEUE}.retry')
RABBITMQ_PARKED_QUEUE = env('RABBITMQ_PARKED_QUEUE', f'{RABBITMQ_QUEUE}.parked')
RABBITMQ_HEARTBEAT = env_int('RABBITMQ_HEARTBEAT', 300)

CONNECT_ATTEMPTS = 10
CONNECT_WAIT_SECONDS = 3
CONTENT_TYPE = 'application/json'


# ===========================================
# Connections
# ===========================================
def connection_parameters():
    """Built field by field, so passwords and the "/" vhost need no URL escaping."""
    credentials = pika.PlainCredentials(RABBITMQ_USERNAME, RABBITMQ_PASSWORD)

    ssl_options = None

    if RABBITMQ_SCHEME == 'amqps':
        ssl_options = pika.SSLOptions(ssl.create_default_context(), RABBITMQ_HOST)

    return pika.ConnectionParameters(
        host=RABBITMQ_HOST,
        port=RABBITMQ_PORT,
        virtual_host=RABBITMQ_VHOST,
        credentials=credentials,
        heartbeat=RABBITMQ_HEARTBEAT,
        blocked_connection_timeout=300,
        ssl_options=ssl_options,
    )


def connect(attempts=CONNECT_ATTEMPTS, wait_seconds=CONNECT_WAIT_SECONDS):
    """A blocking connection, retried because the broker is often not ready when the container starts."""
    parameters = connection_parameters()

    for attempt in range(1, attempts + 1):
        try:
            return pika.BlockingConnection(parameters)
        except (pika.exceptions.AMQPConnectionError, socket.gaierror) as e:
            if attempt == attempts:
                raise

            _logger.warning(
                'RabbitMQ connection attempt %d/%d failed: %s; retrying in %ds',
                attempt,
                attempts,
                e,
                wait_seconds,
            )

            time.sleep(wait_seconds)


# ===========================================
# Topology
# ===========================================
def declare_exchange(channel):
    """Idempotent, so the api and the listener can start in any order."""
    channel.exchange_declare(
        exchange=RABBITMQ_EXCHANGE,
        exchange_type='topic',
        durable=True,
    )


def declare_queue(channel):
    """The listener's durable queue, bound with RABBITMQ_ROUTING_KEY, and its retry and parked queues.

    A failed message waits in the retry queue until its expiration, then RabbitMQ dead-letters it
    straight back to the listener's queue. It goes through the default exchange rather than the
    topic exchange, so other applications bound to the exchange never see a retry. The parked queue
    has no consumer: it holds what gave up so you can inspect it and shovel it back. Idempotent.
    """
    declare_exchange(channel)

    channel.queue_declare(
        queue=RABBITMQ_QUEUE,
        durable=True,
    )

    channel.queue_bind(
        exchange=RABBITMQ_EXCHANGE,
        queue=RABBITMQ_QUEUE,
        routing_key=RABBITMQ_ROUTING_KEY,
    )

    channel.queue_declare(
        queue=RABBITMQ_RETRY_QUEUE,
        durable=True,
        arguments={
            'x-dead-letter-exchange': '',
            'x-dead-letter-routing-key': RABBITMQ_QUEUE,
        },
    )

    channel.queue_declare(
        queue=RABBITMQ_PARKED_QUEUE,
        durable=True,
    )


# ===========================================
# Publishing
# ===========================================
def envelope(event_type, data):
    """The message on the bus, the same {type, data} producers POST to /action."""
    return json.dumps({
        'type': event_type,
        'data': data,
    }).encode()


def publish(channel, event_type, data):
    channel.basic_publish(
        exchange=RABBITMQ_EXCHANGE,
        routing_key=event_type,
        body=envelope(event_type, data),
        properties=pika.BasicProperties(
            content_type=CONTENT_TYPE,
            delivery_mode=pika.DeliveryMode.Persistent,
        ),
    )


def publish_to_queue(channel, queue, body, headers, expiration_ms=None):
    """Put a message straight onto one queue through the default exchange, headers attached.

    The expiration is per message so changing the retry delay never conflicts with the arguments
    the queue was declared with.
    """
    channel.basic_publish(
        exchange='',
        routing_key=queue,
        body=body,
        properties=pika.BasicProperties(
            content_type=CONTENT_TYPE,
            delivery_mode=pika.DeliveryMode.Persistent,
            headers=headers,
            expiration=str(expiration_ms) if expiration_ms is not None else None,
        ),
    )


def publish_event(event_type, data):
    """Connect, publish one event and close; the api calls this once per request."""
    connection = connect(attempts=3, wait_seconds=1)

    try:
        channel = connection.channel()

        declare_exchange(channel)

        publish(channel, event_type, data)

        channel.close()
    finally:
        if connection.is_open:
            connection.close()

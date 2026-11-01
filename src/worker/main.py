"""The consumer side (`listener` mode).

Consumes the service's durable queue with manual acks and up to RABBITMQ_PREFETCH messages in flight.
Each message runs on its own thread so the connection thread keeps answering RabbitMQ heartbeats
however long a handler takes; the ack is handed back to the connection thread because pika is not
thread safe.

Every message is validated against the contract again, then handed to the handler of its type. A
message that can never succeed (undecodable, off contract, or a handler raising Unretryable) is
parked straight away. A handler that returns False or raises anything else is retried after
RABBITMQ_RETRY_DELAY seconds, up to RABBITMQ_MAX_RETRIES times, and then parked. Nothing is
redelivered forever and nothing is silently dropped. RabbitMQ delivers at least once, so a handler
must be safe to run twice.
"""
import functools
import json
import logging
import signal
import sys
import threading
import time

import pika

from src import broker
from src.contracts import validate_event
from src.settings import env_int, setup_logging
from src.worker.target_client import TargetClient, TargetError

_logger = logging.getLogger(__name__)

# how many messages run at once; above 1 your handlers must be thread safe
RABBITMQ_PREFETCH = env_int('RABBITMQ_PREFETCH', 1)

# how many times a failed message is tried again before it is parked; 0 parks on the first failure
RABBITMQ_MAX_RETRIES = env_int('RABBITMQ_MAX_RETRIES', 5)

# seconds a failed message waits in the retry queue
RABBITMQ_RETRY_DELAY = env_int('RABBITMQ_RETRY_DELAY', 30)

RECONNECT_WAIT_SECONDS = 5

# a retried message comes back with the queue name as routing key, so the type rides in a header
EVENT_TYPE_HEADER = 'x-event-type'
RETRY_COUNT_HEADER = 'x-retry-count'
PARK_REASON_HEADER = 'x-park-reason'

OUTCOME_DONE = 'done'
OUTCOME_RETRY = 'retry'
OUTCOME_PARK = 'park'

target = TargetClient()


class Unretryable(Exception):
    """Raised by a handler when trying again cannot help; the message is parked straight away."""


# ===========================================
# Handlers
# ===========================================
# TEMPLATE: one handler per event, returning True when the message is dealt with, False to retry it
# later, or raising Unretryable when it can never succeed
def handle_example_created(data):
    try:
        target.upsert_example(data)
    except TargetError as e:
        if not e.retryable:
            raise Unretryable(f'example {data["id"]} refused: {e}') from e

        _logger.warning('example %s not synced: %s', data['id'], e)

        return False

    _logger.info('example %s synced', data['id'])

    return True


# TEMPLATE: one entry per event in x-event-schemas
EVENT_HANDLERS = {
    'example.created': handle_example_created,
}


# ===========================================
# Processing
# ===========================================
def headers_of(properties):
    if properties is None or not properties.headers:
        return {}

    return dict(properties.headers)


def event_type_of(method, properties):
    return headers_of(properties).get(EVENT_TYPE_HEADER) or method.routing_key


def retry_count_of(properties):
    try:
        return int(headers_of(properties).get(RETRY_COUNT_HEADER, 0))
    except (TypeError, ValueError):
        return 0


def process(event_type, body):
    """Run one message through its contract and handler and say what should happen to it.

    Returns (outcome, reason). Never raises, so a bad message cannot take the worker down.
    """
    handler = EVENT_HANDLERS.get(event_type)

    # the queue may be bound wider than this worker handles
    if handler is None:
        _logger.info('ignoring untracked event type %s', event_type)
        return OUTCOME_DONE, None

    try:
        envelope = json.loads(body)
        data = envelope['data']
    except (ValueError, KeyError, TypeError) as e:
        return OUTCOME_PARK, f'undecodable: {e}'

    errors = validate_event(event_type, data)

    if errors:
        return OUTCOME_PARK, 'off contract: ' + '; '.join(errors)

    try:
        done = handler(data)
    except Unretryable as e:
        return OUTCOME_PARK, str(e)
    except Exception as e:  # noqa: BLE001 - one bad message must not take the consumer down
        _logger.exception('unexpected error processing %s', event_type)
        return OUTCOME_RETRY, f'handler raised {e!r}'

    if not done:
        return OUTCOME_RETRY, 'handler returned False'

    return OUTCOME_DONE, None


# ===========================================
# Settling
# ===========================================
def settle(channel, method, properties, body, outcome, reason):
    """Ack the message, after moving it to the retry or parked queue when it failed.

    Runs on the connection thread. The move is confirmed by the broker before the ack, so a
    failed message is never lost between the two; when the move is refused the message is
    requeued instead.
    """
    if not channel.is_open:
        _logger.warning(
            'channel closed before delivery %s was settled; RabbitMQ will redeliver it',
            method.delivery_tag
        )

        return

    event_type = event_type_of(method, properties)
    retries = retry_count_of(properties)

    if outcome == OUTCOME_RETRY and retries >= RABBITMQ_MAX_RETRIES:
        outcome = OUTCOME_PARK
        reason = f'{reason} (gave up after {retries} retries)'

    headers = headers_of(properties)
    headers[EVENT_TYPE_HEADER] = event_type

    try:
        if outcome == OUTCOME_RETRY:
            headers[RETRY_COUNT_HEADER] = retries + 1

            broker.publish_to_queue(
                channel,
                broker.RABBITMQ_RETRY_QUEUE,
                body,
                headers,
                expiration_ms=RABBITMQ_RETRY_DELAY * 1000
            )

            _logger.warning(
                '%s retry %d/%d in %ds: %s',
                event_type,
                retries + 1,
                RABBITMQ_MAX_RETRIES,
                RABBITMQ_RETRY_DELAY,
                reason
            )

        if outcome == OUTCOME_PARK:
            headers[PARK_REASON_HEADER] = reason

            broker.publish_to_queue(
                channel,
                broker.RABBITMQ_PARKED_QUEUE,
                body,
                headers
            )

            _logger.error('%s parked in %s: %s', event_type, broker.RABBITMQ_PARKED_QUEUE, reason)
    except (pika.exceptions.NackError, pika.exceptions.UnroutableError) as e:
        _logger.error('could not move %s to the %s queue, requeueing it: %s', event_type, outcome, e)
        channel.basic_nack(method.delivery_tag, requeue=True)
        return

    channel.basic_ack(method.delivery_tag)


# ===========================================
# Consumer
# ===========================================
def work(channel, method, properties, body):
    """The message's thread: process it, then hand the settling back to the connection thread."""
    outcome, reason = process(event_type_of(method, properties), body)

    settle_message = functools.partial(
        settle,
        channel,
        method,
        properties,
        body,
        outcome,
        reason
    )

    try:
        channel.connection.add_callback_threadsafe(settle_message)
    except pika.exceptions.AMQPError as e:
        _logger.warning(
            'connection gone before delivery %s was settled; RabbitMQ will redeliver it: %s',
            method.delivery_tag,
            e
        )


def callback(channel, method, properties, body):
    """Start a thread per message, so the connection thread is free to keep heartbeats flowing."""
    thread = threading.Thread(
        target=work,
        args=(channel, method, properties, body),
        name=f'message-{method.delivery_tag}',
        daemon=True
    )

    thread.start()


def consume(connection):
    channel = connection.channel()

    broker.declare_queue(channel)

    # the broker confirms every move to the retry and parked queues before the original is acked
    channel.confirm_delivery()

    # at most this many unacked messages, which is also how many threads run at once
    channel.basic_qos(prefetch_count=RABBITMQ_PREFETCH)

    channel.basic_consume(
        queue=broker.RABBITMQ_QUEUE,
        on_message_callback=callback,
        auto_ack=False,
    )

    _logger.info(
        'waiting for events on %s (exchange %s, binding %s, prefetch %d, %d retries %ds apart)',
        broker.RABBITMQ_QUEUE,
        broker.RABBITMQ_EXCHANGE,
        broker.RABBITMQ_ROUTING_KEY,
        RABBITMQ_PREFETCH,
        RABBITMQ_MAX_RETRIES,
        RABBITMQ_RETRY_DELAY,
    )

    channel.start_consuming()


def consume_forever():
    """Consume until interrupted, reconnecting when the broker goes away.

    Messages still running when the connection closes were never acked, so RabbitMQ delivers them
    again on the next connection.
    """
    while True:
        connection = None

        try:
            connection = broker.connect()

            consume(connection)

            return
        except KeyboardInterrupt:
            _logger.info('shutting down')
            return
        except (pika.exceptions.AMQPError, OSError) as e:
            _logger.warning('RabbitMQ connection lost (%s), reconnecting in %ds', e, RECONNECT_WAIT_SECONDS)
            time.sleep(RECONNECT_WAIT_SECONDS)
        finally:
            if connection is not None and connection.is_open:
                connection.close()


def terminate(signum, frame):
    """SIGTERM from docker stop takes the KeyboardInterrupt path."""
    raise KeyboardInterrupt


def main():
    setup_logging()

    signal.signal(signal.SIGTERM, terminate)

    _logger.info(
        'listener starting: exchange %s, queue %s, target %s',
        broker.RABBITMQ_EXCHANGE,
        broker.RABBITMQ_QUEUE,
        target.base_url or 'not configured',
    )

    consume_forever()

    sys.exit(0)


if __name__ == '__main__':
    main()

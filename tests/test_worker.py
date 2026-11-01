"""The worker with pika mocked: the handler registry, the placeholder handler, what each message
turns into, how it is settled and the thread handoff.

TEMPLATE: replace TestHandlers with one class per handler; the rest stays.

    python -m unittest discover -s tests -v
"""
import json
import threading
import unittest
from unittest import mock

import pika

from src.contracts import OPENAPI_DOCUMENT
from src.worker import main as worker
from src.worker.target_client import TargetError

EVENT_TYPE = 'example.created'
EXAMPLE = {
    'id': 'example-1',
    'name': 'An example',
}


class FakeMethod:

    def __init__(self, routing_key, delivery_tag=1):
        self.routing_key = routing_key
        self.delivery_tag = delivery_tag


def envelope_body(event_type, data):
    return json.dumps({
        'type': event_type,
        'data': data,
    }).encode()


class TestRegistry(unittest.TestCase):

    def test_handlers_cover_every_contract_event(self):
        self.assertEqual(sorted(worker.EVENT_HANDLERS), sorted(OPENAPI_DOCUMENT['x-event-schemas']))


class TestHandlers(unittest.TestCase):

    def test_synced_event_is_done(self):
        with mock.patch.object(worker.target, 'upsert_example', return_value={'ok': True}) as upsert:
            self.assertTrue(worker.handle_example_created(EXAMPLE))

        upsert.assert_called_once_with(EXAMPLE)

    def test_target_outage_is_not_done(self):
        with mock.patch.object(worker.target, 'upsert_example', side_effect=TargetError('down', status=503)):
            self.assertFalse(worker.handle_example_created(EXAMPLE))

    def test_target_refusal_is_unretryable(self):
        with mock.patch.object(worker.target, 'upsert_example', side_effect=TargetError('bad', status=422)):
            with self.assertRaises(worker.Unretryable):
                worker.handle_example_created(EXAMPLE)


class TestProcess(unittest.TestCase):
    """What each kind of message turns into: done, retry or park."""

    def setUp(self):
        self.handler = mock.Mock(return_value=True)

        patcher = mock.patch.dict(worker.EVENT_HANDLERS, {EVENT_TYPE: self.handler})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_valid_message_runs_the_handler_and_is_done(self):
        outcome, reason = worker.process(EVENT_TYPE, envelope_body(EVENT_TYPE, EXAMPLE))

        self.handler.assert_called_once_with(EXAMPLE)
        self.assertEqual(outcome, worker.OUTCOME_DONE)
        self.assertIsNone(reason)

    def test_untracked_event_is_done_without_a_handler(self):
        outcome, _reason = worker.process('some.other.event', envelope_body('some.other.event', {}))

        self.handler.assert_not_called()
        self.assertEqual(outcome, worker.OUTCOME_DONE)

    def test_undecodable_body_is_parked(self):
        outcome, reason = worker.process(EVENT_TYPE, b'not json at all')

        self.handler.assert_not_called()
        self.assertEqual(outcome, worker.OUTCOME_PARK)
        self.assertIn('undecodable', reason)

    def test_envelope_without_data_is_parked(self):
        outcome, _reason = worker.process(EVENT_TYPE, json.dumps({'type': EVENT_TYPE}).encode())

        self.handler.assert_not_called()
        self.assertEqual(outcome, worker.OUTCOME_PARK)

    def test_off_contract_payload_is_parked_before_the_handler(self):
        outcome, reason = worker.process(EVENT_TYPE, envelope_body(EVENT_TYPE, {'name': 'no id'}))

        self.handler.assert_not_called()
        self.assertEqual(outcome, worker.OUTCOME_PARK)
        self.assertIn('off contract', reason)

    def test_failed_handler_is_retried(self):
        self.handler.return_value = False

        outcome, _reason = worker.process(EVENT_TYPE, envelope_body(EVENT_TYPE, EXAMPLE))

        self.assertEqual(outcome, worker.OUTCOME_RETRY)

    def test_handler_exception_is_contained_and_retried(self):
        self.handler.side_effect = RuntimeError('boom')

        outcome, reason = worker.process(EVENT_TYPE, envelope_body(EVENT_TYPE, EXAMPLE))

        self.assertEqual(outcome, worker.OUTCOME_RETRY)
        self.assertIn('boom', reason)

    def test_unretryable_handler_is_parked(self):
        self.handler.side_effect = worker.Unretryable('refused')

        outcome, reason = worker.process(EVENT_TYPE, envelope_body(EVENT_TYPE, EXAMPLE))

        self.assertEqual(outcome, worker.OUTCOME_PARK)
        self.assertEqual(reason, 'refused')


class TestSettle(unittest.TestCase):
    """Every settled message is acked; a failed one is moved to the retry or parked queue first."""

    def setUp(self):
        self.channel = mock.Mock()
        self.channel.is_open = True
        self.body = envelope_body(EVENT_TYPE, EXAMPLE)

        patcher = mock.patch.multiple(worker, RABBITMQ_MAX_RETRIES=3, RABBITMQ_RETRY_DELAY=10)
        patcher.start()
        self.addCleanup(patcher.stop)

    def settle(self, outcome, properties=None, routing_key=EVENT_TYPE):
        worker.settle(
            self.channel,
            FakeMethod(routing_key),
            properties,
            self.body,
            outcome,
            'why'
        )

    def published(self):
        return self.channel.basic_publish.call_args.kwargs

    def test_done_is_acked_and_nothing_is_published(self):
        self.settle(worker.OUTCOME_DONE)

        self.channel.basic_publish.assert_not_called()
        self.channel.basic_ack.assert_called_once_with(1)

    def test_first_retry_goes_to_the_retry_queue_with_a_delay(self):
        self.settle(worker.OUTCOME_RETRY)

        published = self.published()

        self.assertEqual(published['exchange'], '')
        self.assertEqual(published['routing_key'], worker.broker.RABBITMQ_RETRY_QUEUE)
        self.assertEqual(published['body'], self.body)
        self.assertEqual(published['properties'].expiration, '10000')
        self.assertEqual(published['properties'].headers[worker.RETRY_COUNT_HEADER], 1)
        self.assertEqual(published['properties'].headers[worker.EVENT_TYPE_HEADER], EVENT_TYPE)
        self.channel.basic_ack.assert_called_once_with(1)

    def test_returning_retry_keeps_its_event_type_and_counts_up(self):
        properties = pika.BasicProperties(headers={
            worker.EVENT_TYPE_HEADER: EVENT_TYPE,
            worker.RETRY_COUNT_HEADER: 2,
        })

        self.settle(worker.OUTCOME_RETRY, properties, routing_key=worker.broker.RABBITMQ_QUEUE)

        headers = self.published()['properties'].headers

        self.assertEqual(headers[worker.EVENT_TYPE_HEADER], EVENT_TYPE)
        self.assertEqual(headers[worker.RETRY_COUNT_HEADER], 3)

    def test_last_retry_is_parked_instead(self):
        properties = pika.BasicProperties(headers={
            worker.RETRY_COUNT_HEADER: 3,
        })

        self.settle(worker.OUTCOME_RETRY, properties)

        published = self.published()

        self.assertEqual(published['routing_key'], worker.broker.RABBITMQ_PARKED_QUEUE)
        self.assertIsNone(published['properties'].expiration)
        self.assertIn('gave up after 3 retries', published['properties'].headers[worker.PARK_REASON_HEADER])
        self.channel.basic_ack.assert_called_once_with(1)

    def test_park_goes_to_the_parked_queue_with_the_reason(self):
        self.settle(worker.OUTCOME_PARK)

        published = self.published()

        self.assertEqual(published['routing_key'], worker.broker.RABBITMQ_PARKED_QUEUE)
        self.assertEqual(published['properties'].headers[worker.PARK_REASON_HEADER], 'why')
        self.channel.basic_ack.assert_called_once_with(1)

    def test_refused_move_requeues_instead_of_acking(self):
        self.channel.basic_publish.side_effect = pika.exceptions.NackError([])

        self.settle(worker.OUTCOME_PARK)

        self.channel.basic_ack.assert_not_called()
        self.channel.basic_nack.assert_called_once_with(1, requeue=True)

    def test_closed_channel_is_never_acked(self):
        self.channel.is_open = False

        self.settle(worker.OUTCOME_DONE)

        self.channel.basic_ack.assert_not_called()


class TestCallback(unittest.TestCase):
    """The handler runs off the connection thread; settling is handed back to it."""

    def setUp(self):
        self.channel = mock.Mock()
        self.channel.is_open = True
        self.handler = mock.Mock(return_value=True)

        patcher = mock.patch.dict(worker.EVENT_HANDLERS, {EVENT_TYPE: self.handler})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_handler_runs_on_another_thread(self):
        threads = []

        self.handler.side_effect = lambda data: threads.append(threading.current_thread()) or True

        worker.callback(self.channel, FakeMethod(EVENT_TYPE), None, envelope_body(EVENT_TYPE, EXAMPLE))

        for thread in threading.enumerate():
            if thread.name.startswith('message-'):
                thread.join(timeout=5)

        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())

    def test_settling_is_scheduled_on_the_connection_thread(self):
        worker.work(self.channel, FakeMethod(EVENT_TYPE), None, envelope_body(EVENT_TYPE, EXAMPLE))

        self.channel.basic_ack.assert_not_called()

        settle_message = self.channel.connection.add_callback_threadsafe.call_args.args[0]
        settle_message()

        self.channel.basic_ack.assert_called_once_with(1)

    def test_lost_connection_leaves_the_message_for_redelivery(self):
        self.channel.connection.add_callback_threadsafe.side_effect = pika.exceptions.ConnectionWrongStateError()

        worker.work(self.channel, FakeMethod(EVENT_TYPE), None, envelope_body(EVENT_TYPE, EXAMPLE))

        self.channel.basic_ack.assert_not_called()


class TestConsume(unittest.TestCase):

    def test_prefetch_comes_from_the_setting(self):
        connection = mock.Mock()
        channel = connection.channel.return_value

        with mock.patch.object(worker, 'RABBITMQ_PREFETCH', 4):
            worker.consume(connection)

        channel.confirm_delivery.assert_called_once()
        channel.basic_qos.assert_called_once_with(prefetch_count=4)
        channel.start_consuming.assert_called_once()


class TestConsumeForever(unittest.TestCase):

    def test_reconnects_after_a_lost_connection(self):
        first = mock.Mock()
        first.is_open = False
        second = mock.Mock()
        second.is_open = True

        with mock.patch.object(worker.broker, 'connect', side_effect=[first, second]) as connect:
            with mock.patch.object(worker, 'consume', side_effect=[OSError('gone'), KeyboardInterrupt()]):
                with mock.patch.object(worker.time, 'sleep') as sleep:
                    worker.consume_forever()

        self.assertEqual(connect.call_count, 2)
        sleep.assert_called_once_with(worker.RECONNECT_WAIT_SECONDS)
        second.close.assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)

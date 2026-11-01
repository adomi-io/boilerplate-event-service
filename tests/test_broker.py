"""The RabbitMQ wiring with pika mocked: connection parameters, the publish call, the retry loop.

    python -m unittest discover -s tests -v
"""
import json
import socket
import unittest
from unittest import mock

import pika

from src import broker


class TestConnectionParameters(unittest.TestCase):

    def test_plain_amqp(self):
        with mock.patch.multiple(broker, RABBITMQ_SCHEME='amqp', RABBITMQ_HOST='rabbitmq', RABBITMQ_VHOST='example'):
            parameters = broker.connection_parameters()

        self.assertEqual(parameters.host, 'rabbitmq')
        self.assertEqual(parameters.virtual_host, 'example')
        self.assertIsNone(parameters.ssl_options)

    def test_amqps_sets_ssl_options(self):
        with mock.patch.multiple(broker, RABBITMQ_SCHEME='amqps', RABBITMQ_HOST='broker.example.com'):
            parameters = broker.connection_parameters()

        self.assertIsNotNone(parameters.ssl_options)
        self.assertEqual(parameters.ssl_options.server_hostname, 'broker.example.com')


class TestConnect(unittest.TestCase):

    def test_retries_then_succeeds(self):
        connection = mock.Mock()

        with mock.patch.object(broker.pika, 'BlockingConnection') as blocking:
            blocking.side_effect = [socket.gaierror('no dns yet'), connection]

            with mock.patch.object(broker.time, 'sleep') as sleep:
                self.assertIs(broker.connect(attempts=3, wait_seconds=1), connection)

        self.assertEqual(blocking.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_gives_up_after_the_last_attempt(self):
        with mock.patch.object(broker.pika, 'BlockingConnection') as blocking:
            blocking.side_effect = pika.exceptions.AMQPConnectionError('down')

            with mock.patch.object(broker.time, 'sleep'):
                with self.assertRaises(pika.exceptions.AMQPConnectionError):
                    broker.connect(attempts=2, wait_seconds=0)

        self.assertEqual(blocking.call_count, 2)


class TestPublishEvent(unittest.TestCase):

    def test_declares_the_exchange_and_publishes_a_persistent_envelope(self):
        with mock.patch.object(broker.pika, 'BlockingConnection') as blocking:
            connection = blocking.return_value
            connection.is_open = True
            channel = connection.channel.return_value

            broker.publish_event('example.created', {'id': 'example-1'})

        channel.exchange_declare.assert_called_once_with(
            exchange=broker.RABBITMQ_EXCHANGE,
            exchange_type='topic',
            durable=True,
        )

        publish = channel.basic_publish.call_args.kwargs

        self.assertEqual(publish['exchange'], broker.RABBITMQ_EXCHANGE)
        self.assertEqual(publish['routing_key'], 'example.created')
        self.assertEqual(json.loads(publish['body']), {
            'type': 'example.created',
            'data': {'id': 'example-1'},
        })
        self.assertEqual(publish['properties'].content_type, 'application/json')
        self.assertEqual(publish['properties'].delivery_mode, pika.DeliveryMode.Persistent.value)

        connection.close.assert_called_once()

    def test_closes_the_connection_when_publishing_fails(self):
        with mock.patch.object(broker.pika, 'BlockingConnection') as blocking:
            connection = blocking.return_value
            connection.is_open = True
            connection.channel.return_value.basic_publish.side_effect = RuntimeError('broker went away')

            with self.assertRaises(RuntimeError):
                broker.publish_event('example.created', {'id': 'example-1'})

        connection.close.assert_called_once()


class TestDeclareQueue(unittest.TestCase):

    def test_declares_binds_and_is_durable(self):
        channel = mock.Mock()

        broker.declare_queue(channel)

        channel.exchange_declare.assert_called_once()
        channel.queue_bind.assert_called_once_with(
            exchange=broker.RABBITMQ_EXCHANGE,
            queue=broker.RABBITMQ_QUEUE,
            routing_key=broker.RABBITMQ_ROUTING_KEY,
        )

        self.assertEqual(channel.queue_declare.call_args_list, [
            mock.call(
                queue=broker.RABBITMQ_QUEUE,
                durable=True,
            ),
            mock.call(
                queue=broker.RABBITMQ_RETRY_QUEUE,
                durable=True,
                arguments={
                    'x-dead-letter-exchange': '',
                    'x-dead-letter-routing-key': broker.RABBITMQ_QUEUE,
                },
            ),
            mock.call(
                queue=broker.RABBITMQ_PARKED_QUEUE,
                durable=True,
            ),
        ])


class TestPublishToQueue(unittest.TestCase):

    def test_publishes_through_the_default_exchange_with_headers_and_expiration(self):
        channel = mock.Mock()

        broker.publish_to_queue(channel, 'some.queue', b'{}', {'x-retry-count': 1}, expiration_ms=5000)

        publish = channel.basic_publish.call_args.kwargs

        self.assertEqual(publish['exchange'], '')
        self.assertEqual(publish['routing_key'], 'some.queue')
        self.assertEqual(publish['properties'].headers, {'x-retry-count': 1})
        self.assertEqual(publish['properties'].expiration, '5000')
        self.assertEqual(publish['properties'].delivery_mode, pika.DeliveryMode.Persistent.value)


if __name__ == '__main__':
    unittest.main(verbosity=2)

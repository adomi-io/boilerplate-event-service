<p align="center">
  <a href="https://github.com/new?template_name=boilerplate-event-service&template_owner=adomi-io">
    <img
      src="https://img.shields.io/badge/Use%20this%20template-2ea44f?style=for-the-badge&logo=github&logoColor=white"
      alt="Use this template"
      width="400"
    />
  </a>
</p>

# Adomi - Event Service Boilerplate

This repository is a starter template for a small Python service that takes in events over HTTP, checks them against a contract, puts them on a RabbitMQ queue and processes them in a worker.

Take a copy, describe your events in `openapi.yaml`, write a handler for each one, and you have a background service. Use it for anything that should not run inside a web request: syncing records to a mailing tool or a CRM, sending webhooks, media processing, scraping, AI jobs.

> [!TIP]
> **Where do I start?**
>
> Every place you need to change is marked with a `TEMPLATE:` comment
>
> ```sh
> grep -rn 'TEMPLATE:' .
> ```

# Highlights

- 📜 **One contract**: your events are described once, in `src/contracts/openapi.yaml`. The API, the worker and the AsyncAPI document all use it.
- 🚪 **Bad data stops at the door**: a payload that does not match its contract gets a 422 with the reasons. It never reaches the queue.
- 🔀 **Simple routing**: the event type is the routing key, so adding an event is a schema and a handler.
- 📣 **Other apps can listen too**: anyone can bind their own queue to the exchange and get a copy of every message.
- 🧯 **No stuck queues, no lost messages**: a failed message is retried a few times after a delay, then parked in a queue you can inspect. Nothing is redelivered forever.
- 🧵 **Long jobs are fine**: every message runs on its own thread, so the RabbitMQ heartbeat keeps going, and `RABBITMQ_PREFETCH` sets how many run at once.
- 🔁 **Reconnects on its own**: the worker waits for RabbitMQ to come up and reconnects when the connection drops.
- 📦 **One image**: the API and the worker run from the same image. The entrypoint picks the mode.
- 🧪 **Tests that don't need RabbitMQ**: everything is unit tested with the broker mocked, and the tests keep passing as you add events.

# Getting started

> [!WARNING]
> This project is made to run via Docker.
> If you're on Windows or macOS, install Docker Desktop:
>
> **[Download Docker Desktop](https://www.docker.com/products/docker-desktop/)**

Use this template to create your own repository. It comes with a GitHub Actions workflow that tests the service and publishes its image to GitHub Container Registry on every push.

## 1) Clone

```sh
git clone git@github.com:your-company/your-service.git
cd your-service
```

## 2) Configure environment

Copy the example `.env.example` to `.env`:

```sh
cp .env.example .env
```

Change a port in there if `5001` or `15672` is taken on your machine.

## 3) Start with Docker Compose

```sh
docker compose up --build
```

This starts the API on http://localhost:5001, the worker, and RabbitMQ with its management UI on http://localhost:15672 (user and password from `.env`).

## 4) Send an event

```sh
curl -X POST http://localhost:5001/action -H 'Content-Type: application/json' -d '{
  "type": "example.created",
  "data": {"id": "example-1", "name": "An example"}
}'
```

The API answers `202` and the worker picks the event up. Until you point `TARGET_API_URL` at something, the call fails, the worker retries it a few times and then parks it in the `<queue>.parked` queue. That is fine for now.

Send one that breaks the contract and it is refused:

```sh
curl -X POST http://localhost:5001/action -H 'Content-Type: application/json' -d '{
  "type": "example.created",
  "data": {"name": "no id"}
}'
```

## 5) Logs + shell access

```sh
docker compose logs -f listener
```

```sh
docker compose exec api /bin/bash
```

# Adding an event

Events live in `src/contracts/openapi.yaml`. Each one needs a payload schema, an envelope that pins its `type`, a line in `x-event-schemas`, an entry in `EventEnvelope` and a request example. The tests tell you when one of the five is missing.

```yaml
x-event-schemas:
  contact.updated: "#/components/schemas/ContactUpdated"

components:
  schemas:
    ContactUpdated:
      title: contact.updated
      type: object
      required: [id]
      properties:
        id:
          type: string
        email:
          type: [string, "null"]

    ContactUpdatedEnvelope:
      type: object
      required: [type, data]
      properties:
        type:
          type: string
          const: contact.updated
        data:
          $ref: "#/components/schemas/ContactUpdated"

    EventEnvelope:
      oneOf:
        - $ref: "#/components/schemas/ContactUpdatedEnvelope"
      discriminator:
        propertyName: type
        mapping:
          contact.updated: "#/components/schemas/ContactUpdatedEnvelope"
```

Keep the producer's id required and everything else optional. An empty value should mean "unchanged", and producers should send the whole record every time, so processing the same event twice does no harm. RabbitMQ can deliver a message twice.

Then write the handler in `src/worker/main.py` and add it to `EVENT_HANDLERS`. A handler returns `True` when the message is dealt with and `False` when it should be tried again later. When trying again cannot help, like a 422 from the outside API, it raises `Unretryable` and the message is parked straight away:

```python
def handle_contact_updated(data):
    try:
        target.upsert_contact(data)
    except TargetError as e:
        if not e.retryable:
            raise Unretryable(f'contact {data["id"]} refused: {e}') from e

        _logger.warning('contact %s not synced: %s', data['id'], e)

        return False

    return True


EVENT_HANDLERS = {
    'contact.updated': handle_contact_updated,
}
```

Keep the logic in small functions you can test without the client. Handlers already run on their own thread, so a job that takes minutes does not stop the RabbitMQ heartbeat. If you raise `RABBITMQ_PREFETCH` above 1, several handlers run at once, so keep them thread safe.

# Calling your API

`src/worker/target_client.py` is the client your handlers call. Rename it and the `TARGET_*` variables in `.env.example` after the API you are talking to, keep `_request` and the error class, and add a short method per call:

```python
def upsert_contact(self, data):
    return self._request('POST', '/contacts', payload=data)
```

`_request` turns a connection error or an error status into a `TargetError`, so handlers only have one thing to catch. `TargetError.retryable` is true for no answer, a 5xx, a 408 or a 429, and false for any other 4xx.

# Protecting the API

Set `SERVICE_API_KEY` and every `POST /action` must carry the same value in an `X-API-Key` header. Leave it empty when only your own services can reach the API.

| status | when |
|---|---|
| 202 | the event is on the exchange |
| 200 | the type is not one this service tracks; the body lists `tracked_events` |
| 400 | `type` or `data` is missing |
| 401 | `SERVICE_API_KEY` is set and the header is missing or wrong |
| 422 | the payload breaks its contract; `details` lists each violation |
| 503 | RabbitMQ could not be reached; nothing was queued |

# Generating client code

The running service serves its contracts, so producers and consumers generate their code from what is enforced:

| route | what |
|---|---|
| `GET /openapi.json` | the OpenAPI 3.1 document |
| `GET /asyncapi.json` | the AsyncAPI 3.0 document of the exchange |
| `GET /contracts` | the payload schema of every event, `$ref`s inlined |
| `GET /contracts/<type>` | one payload schema |

Generate a producer client from the OpenAPI document:

```sh
openapi-generator generate -i src/contracts/openapi.yaml -g typescript-fetch -o /tmp/events-client
```

Or export both documents to files for tools that need them:

```sh
python -m src.contracts contracts-dist/
npx @asyncapi/cli validate contracts-dist/asyncapi.json
npx @asyncapi/cli generate models typescript contracts-dist/asyncapi.json -o models/
```

# Listening from another application

The API publishes every event to one topic exchange (`RABBITMQ_EXCHANGE`) with the event type as the routing key. Declare your own durable queue, bind it to the exchange with the routing keys you want (`#` for everything) and consume. Each queue gets its own copy of every message, so you never take work away from the worker.

The worker does the same thing with `RABBITMQ_QUEUE` and `RABBITMQ_ROUTING_KEY`. Retries never go back through the exchange, so another application only ever sees each event once.

# Retries and parked messages

Next to its own queue the worker declares two more:

| queue | what |
|---|---|
| `<queue>.retry` | failed messages wait here for `RABBITMQ_RETRY_DELAY` seconds, then RabbitMQ moves them back to `<queue>` |
| `<queue>.parked` | messages that gave up; nothing consumes it, so they stay until you look |

What happens to a message:

| when | what |
|---|---|
| the handler returns `True` | acked |
| the handler returns `False` or raises | sent to the retry queue, up to `RABBITMQ_MAX_RETRIES` times, then parked |
| the handler raises `Unretryable` | parked straight away |
| the body is not JSON, or the payload breaks its contract | parked straight away, because trying again gives the same answer |
| the event type is not one the worker handles | acked |

A message carries its retry count in an `x-retry-count` header, and a parked message says why in `x-park-reason`. The worker only acks the original once RabbitMQ has confirmed the copy in the retry or parked queue, so a failed message is never lost in between. Open the parked queue in the management UI to read the messages, then shovel them back to `<queue>` once you have fixed the cause.

Each message runs on its own thread, and the ack goes back to the connection thread, so heartbeats keep flowing however long a handler takes. `RABBITMQ_PREFETCH` is how many messages the worker holds at once, and so how many handlers run in parallel. A message still running when the worker stops was never acked, so RabbitMQ delivers it again.

> [!NOTE]
> RabbitMQ closes the channel when a message stays unacked longer than its `consumer_timeout`, 30 minutes by default. If one job can run longer than that, raise it on the broker.

With retries, or more than one message in flight, an older version of a record can land after a newer one. If that matters for your target, send an `updated_at` with every event and have the target ignore anything older than what it has.

# Running the tests

The unit tests mock RabbitMQ and the outside API, so they run anywhere:

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

The contract, API and broker tests do not need to change as you add events. Replace the handler tests in `tests/test_worker.py` and the client tests in `tests/test_target_client.py` with your own.

The smoke test starts a built image in both modes and checks it answers:

```sh
docker build -t event-service:dev .
tests/smoke.sh event-service:dev
```

# Building and deploying the image

On every push to `main`, GitHub Actions runs the unit tests, builds the image for `linux/amd64` and `linux/arm64`, smoke tests it and publishes one multi-platform image to `ghcr.io/<owner>/<repository>` tagged `latest`, the date and the short commit sha. A pull request runs the tests and an amd64 build without pushing. Run the workflow by hand to pick the platforms, override the tag or skip the tests.

Dependabot opens a pull request when a Python requirement or an action has a new version, and the auto-merge workflow merges it once every check has passed.

To run the service, start the image twice with the variables from `.env.example`: once as the API and once as the worker. They can start in any order.

```sh
docker run -d --env-file .env -p 5000:5000 ghcr.io/your-company/your-service:latest api
docker run -d --env-file .env ghcr.io/your-company/your-service:latest listener
```

# Adomi

Adomi is an Odoo partner and consulting company. We try to make developing Odoo apps and business utilities as easy as possible. This boilerplate is how we build the background services that run next to Odoo.

Check out some of our other projects:

- **[adomi-io on GitHub](https://github.com/adomi-io)**
- **[adomi-io/odoo](https://github.com/adomi-io/odoo)**: our Odoo image
- **[adomi-io/boilerplate-odoo](https://github.com/adomi-io/boilerplate-odoo)**: a starter for an Odoo project

# One image, the mode picked by the entrypoint (`api` or `listener`).
# TEMPLATE: rename the image with the service
ARG PYTHON_IMAGE=python:3.12-slim


# Wheels are built here so the runtime image needs no compiler
FROM ${PYTHON_IMAGE} AS builder

WORKDIR /build

# Only the requirements, so this layer is reused until they change
COPY requirements.txt .

# Build the wheels with a cached pip download directory
RUN --mount=type=cache,target=/root/.cache/pip \
    pip wheel --wheel-dir /wheels -r requirements.txt


# The image that runs
FROM ${PYTHON_IMAGE}

WORKDIR /app

# Unbuffered logs, no .pyc files next to the code
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install the wheels built above, then drop them
COPY --from=builder /wheels /wheels

RUN pip install --no-cache-dir --no-index --find-links /wheels /wheels/*.whl \
    && rm -rf /wheels

# The service itself
COPY src /app/src
COPY entrypoint.sh /app/entrypoint.sh

# Run as an unprivileged user
RUN chmod +x /app/entrypoint.sh \
    && useradd --create-home --uid 1000 service

USER service

# The api listens here; the listener opens no port
EXPOSE 5000

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]

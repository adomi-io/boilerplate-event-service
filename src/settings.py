"""Configuration, read from the environment on access so a test can set a variable before importing
the module that uses it."""
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv()

TRUE_VALUES = ('1', 'true', 'yes', 'on')
LOG_FORMAT = '%(asctime)s %(levelname)s %(name)s: %(message)s'

# client libraries whose info logs are noise
NOISY_LOGGERS = ('pika', 'urllib3')


def env(name, default=None):
    """The variable's value stripped, `default` when unset or empty."""
    value = os.environ.get(name)

    if value is None or not value.strip():
        return default

    return value.strip()


def env_int(name, default):
    value = env(name)

    if value is None:
        return default

    return int(value)


def env_bool(name, default=False):
    value = env(name)

    if value is None:
        return default

    return value.lower() in TRUE_VALUES


def setup_logging():
    """One line per record on stdout, the level from LOG_LEVEL."""
    level = env('LOG_LEVEL', 'INFO').upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers[:] = [handler]

    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)

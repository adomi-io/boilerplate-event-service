"""The contracts of the service.

    openapi.yaml   the contract, the one file here a service edits
    openapi.py     loads it and builds the validators
    asyncapi.py    derives the AsyncAPI document of the bus from it
    __main__.py    `python -m src.contracts [dir]` exports both as JSON
"""
from src.contracts.asyncapi import ASYNCAPI_DOCUMENT
from src.contracts.openapi import EVENT_SCHEMAS, OPENAPI_DOCUMENT, validate_event

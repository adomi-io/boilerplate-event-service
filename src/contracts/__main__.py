"""Exports the contracts as JSON for codegen without a running service:

    python -m src.contracts [output_dir]      # default: contracts-dist/
"""
import json
import pathlib
import sys

from src.contracts import ASYNCAPI_DOCUMENT, EVENT_SCHEMAS, OPENAPI_DOCUMENT

DEFAULT_OUTPUT_DIR = 'contracts-dist'

DOCUMENTS = {
    'openapi.json': OPENAPI_DOCUMENT,
    'asyncapi.json': ASYNCAPI_DOCUMENT,
    'events.json': EVENT_SCHEMAS,
}


def main(argv):
    output_dir = pathlib.Path(argv[1] if len(argv) > 1 else DEFAULT_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    for filename, document in DOCUMENTS.items():
        path = output_dir / filename

        path.write_text(json.dumps(document, indent=2) + '\n')

        print(path)


if __name__ == '__main__':
    main(sys.argv)

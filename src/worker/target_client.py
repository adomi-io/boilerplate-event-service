"""Thin HTTP client for the outside API the worker talks to.

TEMPLATE: rename Target after the API you call, keep `_request` and the error class, and add one
short method per call that returns parsed data or raises.
"""
import logging

import requests

from src.settings import env, env_int

_logger = logging.getLogger(__name__)

TARGET_API_URL = env('TARGET_API_URL')
TARGET_API_KEY = env('TARGET_API_KEY')
TARGET_API_TIMEOUT = env_int('TARGET_API_TIMEOUT', 30)
ERROR_TEXT_LIMIT = 500

# 4xx statuses that can succeed on a later try: timeouts and rate limits
RETRYABLE_STATUSES = (
    408,
    429,
)


class TargetError(Exception):
    """An error status from the API, or no answer at all (status 0)."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self):
        """No answer, a server error or a rate limit. Any other 4xx will fail the same way again."""
        return self.status == 0 or self.status >= 500 or self.status in RETRYABLE_STATUSES


class TargetClient:

    def __init__(self, base_url=TARGET_API_URL, api_key=TARGET_API_KEY, timeout=TARGET_API_TIMEOUT):
        self.base_url = (base_url or '').rstrip('/')
        self.api_key = api_key
        self.timeout = timeout

    # ===========================================
    # Wiring
    # ===========================================
    def _headers(self):
        headers = {
            'Content-Type': 'application/json',
        }

        # TEMPLATE: the API's authentication header
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'

        return headers

    def _request(self, method, path, payload=None):
        """The parsed JSON body, or TargetError for any failure."""
        if not self.base_url:
            raise TargetError('TARGET_API_URL is not set')

        try:
            response = requests.request(
                method,
                self.base_url + path,
                json=payload,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            raise TargetError(f'target unreachable: {e}') from e

        if response.status_code >= 400:
            raise TargetError(
                (response.text or f'target error {response.status_code}')[:ERROR_TEXT_LIMIT],
                status=response.status_code,
            )

        if not response.content:
            return None

        return response.json()

    # ===========================================
    # Calls
    # ===========================================
    # TEMPLATE: one method per call
    def upsert_example(self, data):
        return self._request('POST', '/examples', payload=data)

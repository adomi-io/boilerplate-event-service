import logging

# The api and worker modules configure root logging at import time; keep
# the test run's output to just the test results.
logging.disable(logging.CRITICAL)

import os
from httpx import Timeout


ACCEPTED_EVENT_STATUS = ["connect", "chat", "disconnect"]

LOG_FILE_PATH = "app/logs"

# BASE_URL = os.getenv("BASE_URL")

BASE_URL = {}

timeout = Timeout(120.0, read=None)

session = {}

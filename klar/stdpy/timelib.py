import time
from datetime import datetime, timezone


def now():
    return time.time()


def millis():
    return int(time.monotonic() * 1000)


def sleep(seconds):
    time.sleep(max(0.0, seconds))


def format_utc(ts, pattern):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(pattern)


def iso_utc(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

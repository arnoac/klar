"""Python side of `extern "klar:web.*"` intrinsics (the JavaScript backend has its own copies)."""

import mimetypes
import urllib.parse


def form_decode(text):
    return {k: v for k, v in urllib.parse.parse_qsl(text, keep_blank_values=True)}


def url_encode(s):
    return urllib.parse.quote(s, safe="")


def url_decode(s):
    return urllib.parse.unquote_plus(s)


def mime_type(path):
    t = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if t.startswith("text/") or t in ("application/javascript", "application/json", "image/svg+xml"):
        t += "; charset=utf-8"
    return t


def publish(topic, data):
    from . import httplib
    httplib.publish(topic, data)

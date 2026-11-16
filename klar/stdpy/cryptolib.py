"""std.crypto for the interpreter."""

import hashlib
import hmac
import secrets

ITERATIONS = 100_000


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hmac_sha256(key, text):
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def token(n):
    return secrets.token_hex(max(0, n))


def _pbkdf2(password, salt, iterations):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()


def password_hash(password):
    salt = secrets.token_hex(16)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${_pbkdf2(password, salt, ITERATIONS)}"


def password_verify(password, stored):
    parts = stored.split("$")
    if len(parts) != 4 or parts[0] != "pbkdf2_sha256" or not parts[1].isdigit() or not 0 < int(parts[1]) <= 10_000_000:
        return False
    return hmac.compare_digest(_pbkdf2(password, parts[2], int(parts[1])), parts[3])


def equal(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

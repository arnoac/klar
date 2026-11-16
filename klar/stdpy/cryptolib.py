"""std.crypto for the interpreter."""

import hashlib
import hmac
import secrets

# OWASP's 2023 recommendation for PBKDF2-HMAC-SHA-256. Stored hashes carry their own count, so hashes made
# with an older, lower count still verify (crypto_password_needs_rehash says when to upgrade them).
ITERATIONS = 600_000
MIN_TOKEN_BYTES = 16


def sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hmac_sha256(key, text):
    return hmac.new(key.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def token(n):
    if n < MIN_TOKEN_BYTES or n > 1 << 20:
        raise ValueError(f"crypto_token({n}): use between {MIN_TOKEN_BYTES} and 1048576 bytes "
                         f"({MIN_TOKEN_BYTES} bytes = 128 bits is the least that cannot be guessed)")
    return secrets.token_hex(n)


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


def needs_rehash(stored):
    parts = stored.split("$")
    return not (len(parts) == 4 and parts[0] == "pbkdf2_sha256" and parts[1].isdigit() and int(parts[1]) >= ITERATIONS)


def equal(a, b):
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))

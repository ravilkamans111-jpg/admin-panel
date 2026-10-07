"""Verification of Django-format password hashes (`auth_user.password`).

The monoliths use Django's default hasher (PBKDF2-SHA256) and nothing else is
present in the three brand databases today; PBKDF2-SHA1 and Argon2 are
accepted too because Django upgrades/downgrades hashers via settings. Any
other or unusable format (`!...`) fails closed. Verification only — this
service never writes passwords to a brand's `auth_user`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_argon2 = PasswordHasher()

# A real PBKDF2 hash of a throwaway password, verified against when the account
# doesn't exist so a login costs the same whether or not the username is known.
DUMMY_DJANGO_HASH = "pbkdf2_sha256$870000$timingequalise$" + base64.b64encode(
    hashlib.pbkdf2_hmac("sha256", b"unused", b"timingequalise", 870000)
).decode()


def verify_django_password(password: str, encoded: str) -> bool:
    if not encoded or encoded.startswith("!"):
        return False
    algorithm, _, rest = encoded.partition("$")
    try:
        if algorithm in ("pbkdf2_sha256", "pbkdf2_sha1"):
            iterations, salt, expected = rest.split("$", 2)
            digest = hashlib.pbkdf2_hmac(
                "sha256" if algorithm == "pbkdf2_sha256" else "sha1",
                password.encode(), salt.encode(), int(iterations),
            )
            return hmac.compare_digest(base64.b64encode(digest).decode(), expected)
        if algorithm == "argon2":
            return _argon2.verify("$" + rest, password)
    except (ValueError, VerificationError, InvalidHashError):
        return False
    return False


def password_fingerprint(encoded: str) -> str:
    """Short stable tag of the stored hash. Refresh tokens carry it, so changing
    the password (in the monolith's admin) invalidates every outstanding session."""
    return hashlib.sha256(encoded.encode()).hexdigest()[:16]

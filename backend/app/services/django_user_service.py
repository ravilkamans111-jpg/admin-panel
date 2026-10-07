"""Port of Django admin's "Add user" (`UserCreationForm`): username rules and the
default password validators (similarity, min length 8, common, numeric), then an
`auth_user` row with a PBKDF2-SHA256 hash exactly as `django.contrib.auth` writes it."""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PBKDF2_ITERATIONS = 870_000
USERNAME_RE = re.compile(r"^[\w.@+-]+$")
MIN_LENGTH = 8
# Django ships ~20k common passwords; this is the head of that list — enough to catch the usual offenders.
COMMON_PASSWORDS = {
    "password", "12345678", "123456789", "qwerty123", "11111111", "iloveyou", "password1", "1q2w3e4r", "qwertyuiop",
    "abc12345", "admin123", "letmein1", "welcome1", "123123123", "00000000", "qazwsxedc", "zaq12wsx", "1qaz2wsx",
    "admin1234", "12341234", "passw0rd", "qwerty12", "q1w2e3r4", "123qweasd", "password123", "adminadmin",
}


class UserValidationError(Exception):
    """Carries per-field error lists (`{"username": [...], "password2": [...]}`)."""

    def __init__(self, errors: dict[str, list[str]]):
        super().__init__("invalid user")
        self.errors = errors


def make_password(raw: str) -> str:
    salt = secrets.token_urlsafe(12)[:22]
    digest = hashlib.pbkdf2_hmac("sha256", raw.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${base64.b64encode(digest).decode()}"


def _password_errors(password: str, username: str) -> list[str]:
    errors = []
    if len(password) < MIN_LENGTH:
        errors.append(f"Введённый пароль слишком короткий. Он должен содержать как минимум {MIN_LENGTH} символов.")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("Введённый пароль слишком широко распространён.")
    if password.isdigit():
        errors.append("Введённый пароль состоит только из цифр.")
    if username and SequenceMatcher(a=password.lower(), b=username.lower()).quick_ratio() >= 0.7 and (
        SequenceMatcher(a=password.lower(), b=username.lower()).ratio() >= 0.7
    ):
        errors.append("Введённый пароль слишком похож на логин.")
    return errors


async def create_user(session: AsyncSession, *, username: str, password1: str, password2: str) -> dict[str, Any]:
    errors: dict[str, list[str]] = {}
    username = username.strip()
    if not username:
        errors["username"] = ["Обязательное поле."]
    elif len(username) > 150 or not USERNAME_RE.match(username):
        errors["username"] = ["Не более 150 символов. Только буквы, цифры и символы @/./+/-/_."]
    elif (await session.execute(text("SELECT 1 FROM auth_user WHERE username = :u"), {"u": username})).first():
        errors["username"] = ["Пользователь с таким именем уже существует."]
    if not password1:
        errors["password1"] = ["Обязательное поле."]
    else:
        problems = _password_errors(password1, username)
        if problems:
            errors["password1"] = problems
    if not password2:
        errors["password2"] = ["Обязательное поле."]
    elif password1 != password2:
        errors["password2"] = ["Введённые пароли не совпадают."]
    if errors:
        raise UserValidationError(errors)

    now = datetime.now(UTC)
    new_id = (
        await session.execute(
            text(
                "INSERT INTO auth_user (password, is_superuser, username, first_name, last_name, email,"
                " is_staff, is_active, date_joined) VALUES (:p, false, :u, '', '', '', false, true, :now) RETURNING id"
            ),
            {"p": make_password(password1), "u": username, "now": now},
        )
    ).scalar_one()
    return {"id": new_id, "username": username, "is_active": True, "is_staff": False, "is_superuser": False}

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


# --- change form (Django admin "Change user") ------------------------------------------------


def _mask_hash(value: str, show: int = 6) -> str:
    """`django.contrib.auth.hashers.mask_hash`."""
    return value[:show] + "*" * len(value[show:])


def _password_summary(encoded: str) -> dict[str, Any]:
    algorithm, _, rest = encoded.partition("$")
    parts = rest.split("$")
    if algorithm.startswith("pbkdf2") and len(parts) == 3:
        iterations, salt, digest = parts
        return {"algorithm": algorithm, "iterations": int(iterations), "salt": _mask_hash(salt), "hash": _mask_hash(digest)}
    return {"algorithm": algorithm or "unknown", "summary": _mask_hash(encoded, 12) if encoded else "No password set."}


def _perm_label(app_label: str, model: str, name: str) -> str:
    from app.registry.admin_models import all_configs

    verbose = {(c.app, c.model.__name__.lower()): c.verbose_name for c in all_configs()}
    verbose.update({("admin", "logentry"): "log entry", ("auth", "permission"): "permission", ("auth", "group"): "group",
                    ("auth", "user"): "user", ("contenttypes", "contenttype"): "content type", ("sessions", "session"): "session"})
    return f"{app_label} | {verbose.get((app_label, model), model)} | {name}"


async def get_edit_data(session: AsyncSession, user_id: int) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                "SELECT id, username, password, first_name, last_name, email, is_active, is_staff, is_superuser,"
                " last_login, date_joined FROM auth_user WHERE id = :i"
            ),
            {"i": user_id},
        )
    ).first()
    if row is None:
        raise LookupError(f"Пользователь {user_id} не найден")
    groups = (await session.execute(text("SELECT id, name FROM auth_group ORDER BY name"))).all()
    chosen_groups = (await session.execute(text("SELECT group_id FROM auth_user_groups WHERE user_id = :i"), {"i": user_id})).scalars().all()
    perms = (
        await session.execute(
            text(
                "SELECT p.id, ct.app_label, ct.model, p.name FROM auth_permission p"
                " JOIN django_content_type ct ON ct.id = p.content_type_id ORDER BY ct.app_label, ct.model, p.codename"
            )
        )
    ).all()
    chosen_perms = (
        await session.execute(text("SELECT permission_id FROM auth_user_user_permissions WHERE user_id = :i"), {"i": user_id})
    ).scalars().all()

    def iso(value: Any) -> str | None:
        return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value else None)

    return {
        "user": {
            "id": row.id, "username": row.username, "first_name": row.first_name, "last_name": row.last_name,
            "email": row.email, "is_active": bool(row.is_active), "is_staff": bool(row.is_staff),
            "is_superuser": bool(row.is_superuser), "last_login": iso(row.last_login), "date_joined": iso(row.date_joined),
            "password": _password_summary(row.password or ""),
        },
        "groups": {"available": [{"id": g.id, "label": g.name} for g in groups], "chosen": list(chosen_groups)},
        "permissions": {
            "available": [{"id": p.id, "label": _perm_label(p.app_label, p.model, p.name)} for p in perms],
            "chosen": list(chosen_perms),
        },
    }


def _parse_dt(value: str | None, field: str, errors: dict[str, list[str]]) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        errors[field] = ["Введите правильную дату и время."]
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def update_user(session: AsyncSession, *, actor_id: int, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
    """Saves the whole "Change user" form (like Django's `UserChangeForm` + m2m widgets)."""
    errors: dict[str, list[str]] = {}
    current = (await session.execute(text("SELECT username FROM auth_user WHERE id = :i"), {"i": user_id})).first()
    if current is None:
        raise LookupError(f"Пользователь {user_id} не найден")

    username = (data.get("username") or "").strip()
    if not username:
        errors["username"] = ["Обязательное поле."]
    elif len(username) > 150 or not USERNAME_RE.match(username):
        errors["username"] = ["Не более 150 символов. Только буквы, цифры и символы @/./+/-/_."]
    elif (
        await session.execute(text("SELECT 1 FROM auth_user WHERE username = :u AND id <> :i"), {"u": username, "i": user_id})
    ).first():
        errors["username"] = ["Пользователь с таким именем уже существует."]
    if user_id == actor_id and not (data.get("is_active") and data.get("is_staff")):
        errors["is_active"] = ["Нельзя снять с себя активность или статус staff — вы потеряете доступ."]

    last_login = _parse_dt(data.get("last_login"), "last_login", errors)
    date_joined = _parse_dt(data.get("date_joined"), "date_joined", errors) or None
    if date_joined is None and "date_joined" not in errors:
        errors["date_joined"] = ["Обязательное поле."]
    if errors:
        raise UserValidationError(errors)

    await session.execute(
        text(
            "UPDATE auth_user SET username=:username, first_name=:first_name, last_name=:last_name, email=:email,"
            " is_active=:is_active, is_staff=:is_staff, is_superuser=:is_superuser, last_login=:last_login,"
            " date_joined=:date_joined WHERE id=:id"
        ),
        {
            "username": username, "first_name": (data.get("first_name") or "")[:150],
            "last_name": (data.get("last_name") or "")[:150], "email": (data.get("email") or "")[:254],
            "is_active": bool(data.get("is_active")), "is_staff": bool(data.get("is_staff")),
            "is_superuser": bool(data.get("is_superuser")), "last_login": last_login, "date_joined": date_joined,
            "id": user_id,
        },
    )
    for table, column, key in (
        ("auth_user_groups", "group_id", "groups"),
        ("auth_user_user_permissions", "permission_id", "permissions"),
    ):
        await session.execute(text(f"DELETE FROM {table} WHERE user_id = :u"), {"u": user_id})
        for item_id in sorted({int(i) for i in data.get(key) or []}):
            await session.execute(
                text(f"INSERT INTO {table} (user_id, {column}) VALUES (:u, :i)"),
                {"u": user_id, "i": item_id},
            )
    return {"id": user_id, "username": username}


async def set_password(session: AsyncSession, *, user_id: int, password1: str, password2: str) -> None:
    """Django's "change password" form for another user."""
    row = (await session.execute(text("SELECT username FROM auth_user WHERE id = :i"), {"i": user_id})).first()
    if row is None:
        raise LookupError(f"Пользователь {user_id} не найден")
    errors: dict[str, list[str]] = {}
    if not password1:
        errors["password1"] = ["Обязательное поле."]
    else:
        problems = _password_errors(password1, row.username)
        if problems:
            errors["password1"] = problems
    if not password2:
        errors["password2"] = ["Обязательное поле."]
    elif password1 != password2:
        errors["password2"] = ["Введённые пароли не совпадают."]
    if errors:
        raise UserValidationError(errors)
    await session.execute(text("UPDATE auth_user SET password = :p WHERE id = :i"), {"p": make_password(password1), "i": user_id})

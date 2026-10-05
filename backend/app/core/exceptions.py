"""Доменные исключения сервисного слоя.

Слой сервисов (`app/services`) не знает о FastAPI/HTTP — он поднимает эти
исключения, а слой обработчиков (`app/api`) уже сам решает, в какой HTTP-код
их превратить. Это и есть граница между слоями "сервис" и "ручки": сервис
никогда не импортирует `fastapi.HTTPException` напрямую.
"""

from __future__ import annotations


class DomainError(Exception):
    """Базовый класс для всех доменных ошибок сервисного слоя."""


class InvalidCredentialsError(DomainError):
    """Неверный email или пароль при логине."""


class AccountInactiveError(DomainError):
    """Учётная запись существует, но деактивирована."""


class BrandAccessDeniedError(DomainError):
    """У пользователя нет доступа к запрошенному бренду."""


class UnknownBrandError(DomainError):
    """Запрошен brand_id, которого нет в реестре известных брендов."""


class InvalidTokenError(DomainError):
    """JWT невалиден, истёк или имеет не тот scope."""


class RecordNotFoundError(DomainError):
    """Запись (или сама модель) не найдена."""


class InvalidFilterError(DomainError):
    """Запрошен фильтр/поле, не входящее в list_filter модели."""


class AccountLockedError(DomainError):
    """Слишком много неудачных попыток входа — вход временно заблокирован."""


class DuplicateEmailError(DomainError):
    """Сотрудник с таким email уже существует."""


class LastSuperuserError(DomainError):
    """Операция оставила бы систему без активного суперпользователя."""


class SelfModificationError(DomainError):
    """Нельзя деактивировать или разжаловать собственную учётную запись."""

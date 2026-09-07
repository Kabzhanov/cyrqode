"""
Референсная реализация подписи и проверки событий Circle Mark (BUIP).

Решение зафиксировано владельцем протокола в
spec/adr/0001-crypto-and-identifiers.md — этот модуль его реализует, а не
переопределяет. Два крипто-профиля:

  - ``ed25519-sha256-v1``   — Ed25519 (RFC 8032), основной профиль
                               (серверы, SDK, мобильные приложения).
  - ``ecdsa-p256-sha256-v1`` — ECDSA P-256 (FIPS 186-4), профиль для
                               смарт-карт / NFC secure element / HSM,
                               которые массово умеют P-256 и не умеют
                               Ed25519.

Что именно подписывается
-------------------------
Канонические байты события по RFC 8785 (JCS), **без** полей, которые
проставляет сервер при приёме (``received_time``, ``committed_time``,
``stream_sequence`` — их у клиента ещё нет в момент подписания), и **без**
самого поля ``signature`` (иначе подпись подписывала бы саму себя).
Канонизация не переопределяется здесь — используется
``sdk/python/buip_canonical.canonicalize`` как единственный источник
истины (см. spec/canonicalization.md).

Два шага, независимые друг от друга (см. spec/canonicalization.md, раздел
"Что закладывается на будущее"): сначала JSON канонизируется и хэшируется
SHA-256, затем результат (32-байтовый дайджест, не JSON) подписывается
выбранным алгоритмом. Отсюда и формула в имени профиля:
``<алгоритм подписи>-sha256-v1``.

  - Для ``ed25519-sha256-v1`` дайджест SHA-256 подписывается "сырым"
    Ed25519 (сам Ed25519 по RFC 8032 внутри всё равно хэширует вход
    SHA-512 — это часть алгоритма и не настраивается; здесь дайджест
    SHA-256 — это 32-байтовое сообщение, которое Ed25519 подписывает).
    Подпись **детерминирована**: один и тот же вход и один и тот же ключ
    всегда дают побайтово одинаковую подпись.
  - Для ``ecdsa-p256-sha256-v1`` тот же дайджест SHA-256 подписывается
    ECDSA P-256 в режиме prehashed (используется тот же дайджест, который
    уже посчитан, повторного хэширования нет). Подпись ECDSA
    **недетерминирована**: каждый вызов ``sign`` даёт другую подпись
    (используется случайный nonce ``k`` по алгоритму), при этом обе
    подписи одинаково успешно проходят ``verify``.

Внешний API одинаков для обоих профилей — они отличаются только строкой
``suite`` и типом ключа, который вы передаёте/получаете.

Пример
------

    from buip_signing import generate_keypair, sign, verify, SUITE_ED25519

    priv, pub = generate_keypair(SUITE_ED25519)
    event = {..., "crypto_suite": SUITE_ED25519}
    event["signature"] = sign(event, priv, SUITE_ED25519)
    assert verify(event, pub, SUITE_ED25519)
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from buip_canonical import canonicalize

__all__ = [
    "SUITE_ED25519",
    "SUITE_ECDSA_P256",
    "SUPPORTED_SUITES",
    "SERVER_FIELDS",
    "SigningError",
    "signing_input",
    "signing_digest",
    "generate_keypair",
    "sign",
    "verify",
    "private_key_to_pem",
    "public_key_to_pem",
    "public_key_to_raw_b64url",
    "load_private_key_from_pem",
    "load_public_key_from_pem",
    "load_public_key_from_raw_b64url",
]

SUITE_ED25519 = "ed25519-sha256-v1"
SUITE_ECDSA_P256 = "ecdsa-p256-sha256-v1"
SUPPORTED_SUITES = (SUITE_ED25519, SUITE_ECDSA_P256)

# Поля, которые проставляет сервер при приёме события и которых у клиента
# ещё нет в момент подписания (см. ADR 0001, spec/canonicalization.md).
SERVER_FIELDS = ("received_time", "committed_time", "stream_sequence")


class SigningError(ValueError):
    """
    Ошибка использования этого модуля: неизвестный/неподдерживаемый
    crypto_suite, ключ не того типа для выбранного профиля, испорченная
    кодировка подписи и т.п. Это ошибки вызывающего кода, а НЕ результат
    "подпись не совпала" — для отрицательного результата проверки
    ``verify`` возвращает False, а не бросает исключение (кроме случаев
    именно неправильного использования API, перечисленных выше).
    """


# ---------------------------------------------------------------------------
# base64url без padding — используется и для подписи, и для raw-формы
# публичного ключа.
# ---------------------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise SigningError("base64url-значение должно быть строкой")
    padding = "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s + padding)
    except Exception as exc:  # noqa: BLE001 - оборачиваем в свой тип ошибки
        raise SigningError(f"не удалось декодировать base64url: {exc}") from exc


# ---------------------------------------------------------------------------
# Подписываемая часть события
# ---------------------------------------------------------------------------


def _assert_suite_supported(suite: str) -> None:
    if suite not in SUPPORTED_SUITES:
        raise SigningError(
            f"неизвестный или неподдерживаемый crypto_suite: {suite!r}; "
            f"поддерживаются: {', '.join(SUPPORTED_SUITES)}"
        )


def _check_suite_consistency(event: dict[str, Any], suite: str) -> None:
    """
    Если в самом событии уже проставлено crypto_suite, оно ДОЛЖНО совпадать
    с профилем, которым его подписывают/проверяют — иначе получится подпись,
    посчитанная по одному профилю, но заявленная в событии как другой.
    Событие без поля crypto_suite (например при подготовке до его
    простановки) пропускается без проверки.
    """
    declared = event.get("crypto_suite")
    if declared is not None and declared != suite:
        raise SigningError(
            f"event['crypto_suite']={declared!r} не совпадает с "
            f"переданным suite={suite!r}"
        )


def signing_input(event: dict[str, Any]) -> bytes:
    """
    Канонические байты (RFC 8785 / JCS) той части события, которая
    подписывается: событие без серверных полей (``received_time``,
    ``committed_time``, ``stream_sequence``) и без поля ``signature``.

    Канонизация делегируется ``buip_canonical.canonicalize`` без изменений
    её правил.
    """
    if not isinstance(event, dict):
        raise SigningError("event должен быть dict (уже разобранный JSON-объект)")

    excluded = set(SERVER_FIELDS)
    excluded.add("signature")
    signable = {k: v for k, v in event.items() if k not in excluded}
    return canonicalize(signable)


def signing_digest(event: dict[str, Any]) -> bytes:
    """SHA-256(signing_input(event)) как сырые 32 байта."""
    return hashlib.sha256(signing_input(event)).digest()


# ---------------------------------------------------------------------------
# Генерация ключей
# ---------------------------------------------------------------------------


def generate_keypair(suite: str):
    """
    Генерирует пару ключей для профиля ``suite``.

    Возвращает (private_key, public_key) — объекты `cryptography`:
    для ``ed25519-sha256-v1`` — Ed25519PrivateKey/Ed25519PublicKey,
    для ``ecdsa-p256-sha256-v1`` — EllipticCurvePrivateKey/PublicKey (P-256).
    """
    _assert_suite_supported(suite)
    if suite == SUITE_ED25519:
        priv = ed25519.Ed25519PrivateKey.generate()
    else:  # SUITE_ECDSA_P256
        priv = ec.generate_private_key(ec.SECP256R1())
    return priv, priv.public_key()


# ---------------------------------------------------------------------------
# Подпись и проверка
# ---------------------------------------------------------------------------


def sign(event: dict[str, Any], private_key, suite: str) -> str:
    """
    Подписывает событие приватным ключом под профилем ``suite``.

    Возвращает подпись в base64url без padding (пригодную для поля
    ``signature`` события). Само поле ``signature`` в event НЕ
    записывается — это решает вызывающий код.
    """
    _assert_suite_supported(suite)
    _check_suite_consistency(event, suite)
    digest = signing_digest(event)

    if suite == SUITE_ED25519:
        if not isinstance(private_key, ed25519.Ed25519PrivateKey):
            raise SigningError(
                "для ed25519-sha256-v1 нужен ed25519.Ed25519PrivateKey"
            )
        raw_signature = private_key.sign(digest)
    else:  # SUITE_ECDSA_P256
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise SigningError(
                "для ecdsa-p256-sha256-v1 нужен EllipticCurvePrivateKey (P-256)"
            )
        if not isinstance(private_key.curve, ec.SECP256R1):
            raise SigningError(
                "для ecdsa-p256-sha256-v1 требуется кривая P-256 (SECP256R1), "
                f"получена {private_key.curve.name!r}"
            )
        raw_signature = private_key.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))

    return _b64url_encode(raw_signature)


def verify(event: dict[str, Any], public_key, suite: str, signature: str | None = None) -> bool:
    """
    Проверяет подпись события публичным ключом под профилем ``suite``.

    Если ``signature`` не передан явно, берётся ``event["signature"]``.
    Возвращает True/False по факту криптографической проверки; на
    неправильное использование API (неизвестный suite, ключ не того типа,
    битая base64url-кодировка подписи) бросает ``SigningError`` — это не
    "подпись не сошлась", а "проверку в принципе нельзя выполнить".
    """
    _assert_suite_supported(suite)
    _check_suite_consistency(event, suite)

    sig_b64 = signature if signature is not None else event.get("signature")
    if not sig_b64:
        raise SigningError("нет подписи для проверки: ни аргумент signature, ни event['signature']")
    raw_signature = _b64url_decode(sig_b64)

    digest = signing_digest(event)

    try:
        if suite == SUITE_ED25519:
            if not isinstance(public_key, ed25519.Ed25519PublicKey):
                raise SigningError(
                    "для ed25519-sha256-v1 нужен ed25519.Ed25519PublicKey"
                )
            public_key.verify(raw_signature, digest)
        else:  # SUITE_ECDSA_P256
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise SigningError(
                    "для ecdsa-p256-sha256-v1 нужен EllipticCurvePublicKey (P-256)"
                )
            if not isinstance(public_key.curve, ec.SECP256R1):
                raise SigningError(
                    "для ecdsa-p256-sha256-v1 требуется кривая P-256 (SECP256R1), "
                    f"получена {public_key.curve.name!r}"
                )
            public_key.verify(raw_signature, digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    except InvalidSignature:
        return False
    return True


# ---------------------------------------------------------------------------
# Сериализация ключей
# ---------------------------------------------------------------------------


def private_key_to_pem(private_key) -> str:
    """Приватный ключ в PEM, формат PKCS8, без шифрования."""
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pem.decode("ascii")


def public_key_to_pem(public_key) -> str:
    """Публичный ключ в PEM, формат SubjectPublicKeyInfo (SPKI)."""
    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return pem.decode("ascii")


def public_key_to_raw_b64url(public_key) -> str:
    """
    Компактное представление публичного ключа для передачи в резолвере
    (там, где полный PEM избыточен): "сырые" байты ключа в base64url без
    padding.

    Для Ed25519 — 32 байта (RFC 8032). Для ECDSA P-256 — 65 байт,
    несжатая точка кривой в формате SEC1/X9.62 (0x04 || X || Y, по 32
    байта на координату) — стандартное представление
    `cryptography.PublicFormat.UncompressedPoint`, ничего не изобретается.
    """
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    elif isinstance(public_key, ec.EllipticCurvePublicKey):
        raw = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
    else:
        raise SigningError(f"неподдерживаемый тип публичного ключа: {type(public_key)!r}")
    return _b64url_encode(raw)


def load_private_key_from_pem(pem: str):
    """Загружает приватный ключ (Ed25519 или EC P-256) из PEM PKCS8."""
    return serialization.load_pem_private_key(pem.encode("ascii"), password=None)


def load_public_key_from_pem(pem: str):
    """Загружает публичный ключ (Ed25519 или EC P-256) из PEM SPKI."""
    return serialization.load_pem_public_key(pem.encode("ascii"))


def load_public_key_from_raw_b64url(raw_b64url: str, suite: str):
    """Обратная операция к public_key_to_raw_b64url, требует явного suite."""
    _assert_suite_supported(suite)
    raw = _b64url_decode(raw_b64url)
    if suite == SUITE_ED25519:
        return ed25519.Ed25519PublicKey.from_public_bytes(raw)
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)

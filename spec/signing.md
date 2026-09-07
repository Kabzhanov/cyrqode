# Подпись событий BUIP

Решение зафиксировано владельцем протокола в
[`spec/adr/0001-crypto-and-identifiers.md`](adr/0001-crypto-and-identifiers.md)
— этот документ его разъясняет и даёт примеры кода, а не переопределяет.
Референсная реализация: [`sdk/python/buip_signing.py`](../sdk/python/buip_signing.py).
Тестовые векторы: [`test-vectors/signing.json`](../test-vectors/signing.json),
прогон — [`test-vectors/verify.py`](../test-vectors/verify.py).

## Два профиля

| `crypto_suite` | Алгоритм | Где применяется |
|---|---|---|
| `ed25519-sha256-v1` | Ed25519 (RFC 8032) над SHA-256(signing_input) | основной: серверы, SDK, мобильные приложения |
| `ecdsa-p256-sha256-v1` | ECDSA P-256 (FIPS 186-4) над SHA-256(signing_input), prehashed | смарт-карты, NFC secure element, HSM |
| `unsigned-v0.1` | нет подписи | события без подписи (в т.ч. записи журнала CYRQODE, сделанные до этого решения) |

Внешний API одинаков для обоих подписывающих профилей — `sign(event,
private_key, suite)` и `verify(event, public_key, suite)`. Отличается
только значение `suite` и тип ключа (Ed25519 либо EC P-256), который вы
передаёте. Почему два профиля, а не один — см. ADR 0001, раздел «Решение 1».

## Что именно подписывается

Подписывается **не всё событие целиком**, а только та его часть, которую
клиент физически может знать в момент подписания:

1. Из события убираются поля, которые проставляет сервер при приёме:
   `received_time`, `committed_time`, `stream_sequence` — их у клиента ещё
   нет в момент формирования и подписания события.
2. Убирается само поле `signature` (иначе подпись подписывала бы саму
   себя).
3. Оставшийся объект канонизируется по RFC 8785 (JCS) через
   `buip_canonical.canonicalize` — тем же алгоритмом, что описан в
   [`spec/canonicalization.md`](canonicalization.md), без изменений его
   правил.

Это и есть `signing_input(event)`:

```python
def signing_input(event: dict) -> bytes:
    excluded = {"received_time", "committed_time", "stream_sequence", "signature"}
    signable = {k: v for k, v in event.items() if k not in excluded}
    return canonicalize(signable)  # bytes, UTF-8
```

Дальше — SHA-256 от этих байт (`signing_digest`, 32 сырых байта), и уже этот
дайджест подписывается алгоритмом, который определяет `crypto_suite`. Два
шага (канонизация+хэш и собственно подпись) независимы — это специально
подчёркнуто и в `spec/canonicalization.md`.

### Главные грабли: не проверяйте подпись по записи из журнала целиком

Событие, отданное журналом после приёма (`event-committed.schema.json`),
содержит `received_time`, `committed_time` и `stream_sequence` — их там нет
в исходном подписанном событии. Если вы возьмёте такую запись из журнала и
скормите её байты (после вашей собственной пересборки JSON) прямо в
проверку подписи, вы либо:

- включите в подписываемый набор поля, которых не было при подписании (и
  получите другой канонический результат — подпись «не сойдётся», хотя она
  корректна), либо
- если исключаете поля вручную, легко ошибётесь: либо забудете исключить
  `signature`, либо случайно исключите что-то из клиентских полей — оба
  варианта дают неверный результат проверки.

Правильно: **сначала** привести событие к виду `signing_input` (убрать три
серверных поля и `signature`), **потом** проверять. Библиотека делает это
автоматически — `verify()` сама вызывает `signing_input()` внутри, поэтому
ей можно скармливать и «сырое» подписанное событие, и то же событие уже
после приёма сервером (после добавления серверных полей) — результат будет
одинаковым, если содержимое клиентских полей не менялось. Именно это
показано в `test-vectors/signing.json`: поле `event_after_server_commit`
проверяется тем же `verify_expected: true`, что и исходное `event`.

## Пример: подписать и проверить (Ed25519)

```python
import sys
sys.path.insert(0, "sdk/python")

from buip_signing import generate_keypair, sign, verify, SUITE_ED25519

priv, pub = generate_keypair(SUITE_ED25519)

event = {
    "event_id": "buip:evt:acme:0000000000000001",
    "tenant_id": "buip:tenant:acme",
    "namespace_id": "buip:ns:acme",
    "event_type": "document.signed.v1",
    "subject_entity_id": "buip:acme:document:2026-000001",
    "actor_entity_id": "buip:acme:agent:svc-esign-01",
    "actor_authority_scope": "buip:policy:acme:document-signing:v1",
    "event_time": "2026-09-07T09:00:00Z",
    "idempotency_key": "esign-2026-000001:sign",
    "schema_version": "0.1.0",
    "crypto_suite": SUITE_ED25519,
}

event["signature"] = sign(event, priv, SUITE_ED25519)
assert verify(event, pub, SUITE_ED25519) is True

# любое изменение клиентского поля после подписи ломает проверку
tampered = dict(event)
tampered["actor_authority_scope"] = "buip:policy:acme:something-else:v1"
assert verify(tampered, pub, SUITE_ED25519) is False
```

## Пример: ECDSA P-256 (secure element / HSM)

```python
from buip_signing import generate_keypair, sign, verify, SUITE_ECDSA_P256

priv, pub = generate_keypair(SUITE_ECDSA_P256)
event = {..., "crypto_suite": SUITE_ECDSA_P256}
event["signature"] = sign(event, priv, SUITE_ECDSA_P256)
assert verify(event, pub, SUITE_ECDSA_P256) is True
```

API идентично Ed25519-варианту. Разница — только в `suite` и в поведении
самой подписи (см. следующий раздел).

## Ed25519 детерминирован, ECDSA — нет

Это не деталь реализации, а свойство самих алгоритмов, важное для того, как
вы пишете тесты и сравниваете значения:

- **Ed25519** не использует случайность при подписании: один и тот же
  вход и один и тот же приватный ключ **всегда** дают побайтово одинаковую
  подпись. Поэтому в `test-vectors/signing.json` подпись для профиля
  `ed25519-sha256-v1` — это эталон, с которым можно и нужно сравнивать свою
  реализацию побайтово (строковым сравнением base64url).
- **ECDSA P-256** на каждой подписи использует случайный nonce `k`: один и
  тот же вход с тем же ключом даёт **разную** подпись при каждом вызове
  `sign()`, при этом обе подписи одинаково успешно проходят `verify()`.
  Поэтому вектор для `ecdsa-p256-sha256-v1` — это **один конкретный
  пример**, а не эталон для побайтового сравнения. Правильная проверка
  сторонней реализации — это: (а) приведённая в векторе подпись проходит
  `verify()` с приведённым публичным ключом, и (б) собственная подпись той
  же реализации над тем же `signing_input` **тоже** проходит `verify()` с
  тем же ключом. Сравнение строк подписи между двумя ECDSA-реализациями (а
  зачастую и между двумя запусками одной и той же реализации) — гарантированно
  ошибочный тест.

`test-vectors/verify.py` реализует именно эту разницу: подпись
`ed25519-sha256-v1` сравнивается побайтово, подпись
`ecdsa-p256-sha256-v1` — только через `verify()`.

## Сериализация ключей

- Приватный ключ — PEM, PKCS8, без шифрования
  (`private_key_to_pem`/`load_private_key_from_pem`).
- Публичный ключ — PEM, SubjectPublicKeyInfo (SPKI)
  (`public_key_to_pem`/`load_public_key_from_pem`).
- Компактная форма публичного ключа для передачи в резолвере — «сырые»
  байты в base64url без padding (`public_key_to_raw_b64url`/
  `load_public_key_from_raw_b64url`): 32 байта для Ed25519 (RFC 8032), 65
  байт (несжатая точка кривой, `0x04 || X || Y`) для ECDSA P-256.

## Кодировка подписи

`signature` — base64url (RFC 4648 §5) **без padding** (без символов `=`).
Для `ed25519-sha256-v1` это ровно 64 байта подписи Ed25519. Для
`ecdsa-p256-sha256-v1` это подпись в формате ASN.1 DER (`r`, `s` как
`INTEGER`), поэтому её длина в байтах не фиксирована (обычно 70–72 байта
до кодирования).

## Задел на будущее

`crypto_suite` — обязательное поле в каждом событии именно для того, чтобы
добавление постквантового профиля не потребовало менять формат события:
появится третье значение enum, а `signing_input`/канонизация не изменятся.

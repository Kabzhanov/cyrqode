#!/usr/bin/env python3
"""
Прогоняет тестовые векторы канонизации и подписи Circle Mark через
референсные реализации и (опционально, если установлены соответствующие
пакеты) валидирует примеры событий по JSON Schema.

Использование:

    python3 test-vectors/verify.py

Код возврата 0 — всё сошлось. Ненулевой код — есть расхождение в
канонизации (это всегда считается ошибкой), в подписи (если пакет
cryptography доступен) либо (если jsonschema доступен) пример не прошёл
валидацию по схеме.

Проверка канонизации выполняется ВСЕГДА и не может быть пропущена: это
основная гарантия репозитория. Проверка подписи (test-vectors/signing.json
через sdk/python/buip_signing.py) и проверка JSON Schema для examples/*.json
— дополнительные, но не необязательные в смысле важности: если
cryptography или jsonschema не установлены, соответствующая проверка явно
помечается как пропущенная (SKIP), а не тихо считается успешной.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_PATH = REPO_ROOT / "sdk" / "python"
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"
VECTORS_FILE = Path(__file__).resolve().parent / "canonicalization.json"
SIGNING_VECTORS_FILE = Path(__file__).resolve().parent / "signing.json"

sys.path.insert(0, str(SDK_PATH))

from buip_canonical import canonicalize, digest  # noqa: E402


def verify_canonicalization() -> bool:
    print("== Канонизация (RFC 8785 / JCS) ==")
    with open(VECTORS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    cases = data["cases"]
    ok = True
    for case in cases:
        name = case["name"]
        obj = case["input"]
        expected_canonical = case["canonical"]
        expected_sha256 = case["sha256"]

        actual_bytes = canonicalize(obj)
        actual_canonical = actual_bytes.decode("utf-8")
        actual_sha256 = digest(obj)

        case_ok = True
        if actual_canonical != expected_canonical:
            case_ok = False
            print(f"  [FAIL] {name}: canonical mismatch")
            print(f"         expected: {expected_canonical!r}")
            print(f"         actual:   {actual_canonical!r}")
        if actual_sha256 != expected_sha256:
            case_ok = False
            print(f"  [FAIL] {name}: sha256 mismatch")
            print(f"         expected: {expected_sha256}")
            print(f"         actual:   {actual_sha256}")

        if case_ok:
            print(f"  [ok]   {name}")
        else:
            ok = False

    print(f"Итого: {len(cases)} случаев, {'все совпали' if ok else 'ЕСТЬ РАСХОЖДЕНИЯ'}")
    return ok


def verify_signing() -> bool:
    print()
    print("== Подпись событий (test-vectors/signing.json) ==")
    try:
        import buip_signing as sig
    except ImportError as exc:
        print(f"  [SKIP] пакет 'cryptography' недоступен ({exc}) — проверка подписи пропущена")
        print("         (проверка канонизации выше это не затрагивает и обязательна)")
        return True

    with open(SIGNING_VECTORS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    ok = True
    for suite, profile in data["profiles"].items():
        deterministic = profile["deterministic"]
        public_key = sig.load_public_key_from_pem(profile["public_key_pem"])
        private_key = sig.load_private_key_from_pem(profile["private_key_pem"])

        for case in profile["cases"]:
            name = case["name"]
            event = case["event"]
            expected_verify = case["verify_expected"]

            case_ok = True

            # 1. signing_input и его SHA-256, если случай их содержит (позитивные случаи)
            if "signing_input" in case:
                actual_input = sig.signing_input(event)
                expected_input = case["signing_input"].encode("utf-8")
                if actual_input != expected_input:
                    case_ok = False
                    print(f"  [FAIL] {name}: signing_input mismatch")
                if "signing_input_sha256" in case:
                    actual_sha256 = hashlib.sha256(actual_input).hexdigest()
                    if actual_sha256 != case["signing_input_sha256"]:
                        case_ok = False
                        print(f"  [FAIL] {name}: signing_input_sha256 mismatch")

            # 2. Проверка подписи из вектора: всегда через verify(), для обоих профилей.
            actual_verify = sig.verify(event, public_key, suite)
            if actual_verify != expected_verify:
                case_ok = False
                print(
                    f"  [FAIL] {name}: verify()={actual_verify}, "
                    f"ожидалось {expected_verify}"
                )

            # 3. Побайтовое сравнение подписи — ТОЛЬКО для детерминированных
            #    профилей (Ed25519). Для ECDSA такое сравнение in principle
            #    расходится даже у корректной реализации (случайный nonce),
            #    поэтому для неё сравнивать строкой нельзя — см. README.
            if deterministic and "signature_b64url" in case and expected_verify:
                own_signature = sig.sign(event, private_key, suite)
                if own_signature != case["signature_b64url"]:
                    case_ok = False
                    print(f"  [FAIL] {name}: подпись разошлась побайтово (детерминированный профиль)")
            elif not deterministic and "signature_b64url" in case and expected_verify:
                # Недетерминированный профиль: проверяем не байтовое совпадение,
                # а то, что СВОЯ подпись того же входа тоже проходит verify().
                own_signature = sig.sign(event, private_key, suite)
                own_event = dict(event)
                own_event["signature"] = own_signature
                if not sig.verify(own_event, public_key, suite):
                    case_ok = False
                    print(f"  [FAIL] {name}: собственная подпись (ECDSA) не прошла verify()")

            if case_ok:
                print(f"  [ok]   {name}")
            else:
                ok = False

    print(f"Итого: профили {', '.join(data['profiles'].keys())}, {'все совпали' if ok else 'ЕСТЬ РАСХОЖДЕНИЯ'}")
    return ok


def verify_examples_against_schemas() -> bool:
    print()
    print("== Валидация examples/*.json по JSON Schema ==")
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        print("  [SKIP] пакет 'jsonschema' не установлен — валидация схем пропущена")
        print("         (проверка канонизации выше это не затрагивает и обязательна)")
        return True

    # Собираем store всех схем по их $id, чтобы $ref в profile-manufacturing
    # корректно резолвился на event.schema.json без внешних запросов.
    store = {}
    for schema_file in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        with open(schema_file, "r", encoding="utf-8") as f:
            schema = json.load(f)
        schema_id = schema.get("$id")
        if schema_id:
            store[schema_id] = schema

    plan = [
        (EXAMPLES_DIR / "event-generic.json", "event.schema.json"),
        (EXAMPLES_DIR / "event-manufacturing.json", "profile-manufacturing.schema.json"),
    ]

    # Записи публичного реестра проверяются по ЯДРУ: у них нет серверных полей
    # (received_time, committed_time, stream_sequence), потому что они ещё не
    # приняты в журнал. Именно на этом различии и держится разделение схем
    # event.schema.json и event-committed.schema.json.
    registry_dir = REPO_ROOT / "registry"
    if registry_dir.is_dir():
        plan += [(f, "event.schema.json") for f in sorted(registry_dir.glob("*.json"))]

    ok = True
    for example_path, schema_name in plan:
        example_name = example_path.name
        with open(example_path, "r", encoding="utf-8") as f:
            instance = json.load(f)

        schema_path = SCHEMAS_DIR / schema_name
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = json.load(f)

        resolver = jsonschema.validators.RefResolver(
            base_uri=schema.get("$id", schema_path.as_uri()),
            referrer=schema,
            store=store,
        )
        validator = Draft202012Validator(schema, resolver=resolver)
        errors = sorted(validator.iter_errors(instance), key=lambda e: e.path)

        if errors:
            ok = False
            print(f"  [FAIL] {example_name} против {schema_name}:")
            for err in errors:
                loc = "/".join(str(p) for p in err.path) or "<root>"
                print(f"         - {loc}: {err.message}")
        else:
            print(f"  [ok]   {example_name} валиден по {schema_name}")

    return ok


def main() -> int:
    canon_ok = verify_canonicalization()
    signing_ok = verify_signing()
    schema_ok = verify_examples_against_schemas()
    success = canon_ok and signing_ok and schema_ok
    print()
    print("РЕЗУЛЬТАТ: OK" if success else "РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

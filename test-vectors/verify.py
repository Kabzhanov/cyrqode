#!/usr/bin/env python3
"""
Прогоняет тестовые векторы канонизации Circle Mark через референсную
реализацию и (опционально, если установлен пакет jsonschema) валидирует
примеры событий по JSON Schema.

Использование:

    python3 test-vectors/verify.py

Код возврата 0 — всё сошлось. Ненулевой код — есть расхождение в
канонизации (это всегда считается ошибкой) либо (если jsonschema доступен)
пример не прошёл валидацию по схеме.

Проверка канонизации выполняется ВСЕГДА и не может быть пропущена: это
основная гарантия репозитория. Проверка JSON Schema для examples/*.json —
дополнительная, но не необязательная в смысле важности: если jsonschema не
установлен, она явно помечается как пропущенная, а не тихо считается
успешной.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SDK_PATH = REPO_ROOT / "sdk" / "python"
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"
VECTORS_FILE = Path(__file__).resolve().parent / "canonicalization.json"

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
    schema_ok = verify_examples_against_schemas()
    success = canon_ok and schema_ok
    print()
    print("РЕЗУЛЬТАТ: OK" if success else "РЕЗУЛЬТАТ: ЕСТЬ ОШИБКИ")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())

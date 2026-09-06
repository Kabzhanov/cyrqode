"""
Референсная реализация канонизации JSON для Circle Mark (RFC 8785 JCS).

Назначение
----------
Эта реализация — единственный источник истины для того, "что значит
канонизировать объект" в Python. Она не имеет внешних зависимостей (только
стандартная библиотека) и предназначена как эталон, по которому можно
проверять любой другой язык/SDK через test-vectors/canonicalization.json.

Публичный интерфейс:

    canonicalize(obj) -> bytes   # каноническое UTF-8 представление
    digest(obj) -> str           # SHA-256(canonicalize(obj)) в hex

Подробное описание правил канонизации — в spec/canonicalization.md. Здесь
код и docstring-и объясняют "как", там документ объясняет "почему" и
формулирует правила независимо от языка реализации.

Важные ограничения (см. spec/canonicalization.md):

  - Числа канонизируются как IEEE-754 double (как того требует JCS), в том
    числе Python `int`. Значения вне безопасного диапазона double
    (|x| > 2**53) проходят через float() и МОГУТ потерять точность — это
    свойство самого JSON/JCS-числового типа, а не баг данной реализации.
    Для идентификаторов вне этого диапазона используйте строки (так и
    сделано в схемах Circle Mark: entity_id — строка, а не число).
  - NaN и Infinity не являются валидными значениями JSON и вызывают
    ValueError.
  - Порядок ключей объекта задаётся исходным dict; канонизация ключи не
    придумывает и не отбрасывает — она их только сортирует.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

__all__ = ["canonicalize", "digest", "CanonicalizationError"]


class CanonicalizationError(ValueError):
    """Значение нельзя канонизировать по правилам RFC 8785 (например NaN/Infinity)."""


# ---------------------------------------------------------------------------
# Сортировка ключей объекта: RFC 8785 требует сравнения по UTF-16 code units,
# а не по Unicode code point. Для символов вне Basic Multilingual Plane это
# разные порядки (см. spec/canonicalization.md, правило 1). Сравнение по
# big-endian UTF-16 байтам даёт нужный порядок: два байта на code unit,
# сравнение bytes идёт лексикографически побайтово, что для big-endian пары
# байт эквивалентно численному сравнению самого code unit.
# ---------------------------------------------------------------------------


def _utf16_sort_key(key: str) -> bytes:
    """Ключ сортировки, воспроизводящий порядок UTF-16 code units по RFC 8785."""
    return key.encode("utf-16-be", "surrogatepass")


# ---------------------------------------------------------------------------
# Числа: реализация алгоритма, эквивалентного ECMAScript Number::toString,
# на основе кратчайшего round-trip десятичного представления, которое даёт
# repr() для Python float (Python с версии 3.1 использует корректно
# округляющий алгоритм кратчайшего представления, тот же класс алгоритмов,
# что требует и спецификация ECMAScript/JCS).
# ---------------------------------------------------------------------------

_REPR_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:e([+-]?\d+))?$")


def _decompose_float(x: float) -> tuple[str, int]:
    """
    Раскладывает |x| (x > 0, конечное) на (digits, n), где digits — кратчайшая
    значащая десятичная последовательность без ведущих/хвостовых нулей, а n —
    показатель степени такой, что значение равно 0.digits * 10**n.

    Это ровно та форма (s, k, n), которую использует ECMAScript
    Number::toString для выбора между целочисленной, дробной и
    экспоненциальной записью.
    """
    r = repr(x)
    m = _REPR_RE.match(r)
    if not m:  # pragma: no cover - repr(float) всегда даёт этот формат
        raise CanonicalizationError(f"не удалось разобрать repr(float): {r!r}")
    int_part, frac_part, exp_part = m.groups()
    frac_part = frac_part or ""
    exponent = int(exp_part) if exp_part else 0

    digits_raw = int_part + frac_part
    # позиция десятичной точки от начала digits_raw (до учёта ведущих нулей)
    point = len(int_part) + exponent

    stripped_leading = digits_raw.lstrip("0")
    leading_zeros = len(digits_raw) - len(stripped_leading)
    point -= leading_zeros

    digits = stripped_leading.rstrip("0")
    if digits == "":
        # x == 0 обрабатывается отдельно в _format_number, сюда попасть не должно
        digits = "0"

    return digits, point


def _format_number(x: int | float) -> str:
    """Форматирует число по правилам ECMAScript Number::toString (JCS §3.2.2.3)."""
    if isinstance(x, bool):  # bool — подкласс int в Python, но это не число JSON
        raise CanonicalizationError("bool должен обрабатываться отдельно от number")

    value = float(x)

    if value != value:  # NaN
        raise CanonicalizationError("NaN не является допустимым значением JSON")
    if value in (float("inf"), float("-inf")):
        raise CanonicalizationError("Infinity не является допустимым значением JSON")

    if value == 0.0:
        # ECMAScript ToString(-0) == "0": знак минус для нуля не сериализуется
        return "0"

    sign = "-" if value < 0 else ""
    digits, n = _decompose_float(abs(value))
    k = len(digits)

    if k <= n <= 21:
        result = digits + ("0" * (n - k))
    elif 0 < n <= 21:
        result = digits[:n] + "." + digits[n:]
    elif -6 < n <= 0:
        result = "0." + ("0" * (-n)) + digits
    else:
        mantissa = digits if k == 1 else f"{digits[0]}.{digits[1:]}"
        exp = n - 1
        exp_sign = "+" if exp >= 0 else "-"
        result = f"{mantissa}e{exp_sign}{abs(exp)}"

    return sign + result


# ---------------------------------------------------------------------------
# Строки: экранируется минимально необходимый набор символов (RFC 8785,
# наследуется от требований RFC 8259 к валидному JSON-выводу). Не-ASCII
# символы (кириллица, эмодзи и т.д.) выводятся как литеральные UTF-8 байты,
# НЕ как \uXXXX.
# ---------------------------------------------------------------------------

_SHORT_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


def _encode_string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            # включая кириллицу, эмодзи и т.п. — без экранирования
            out.append(ch)
    out.append('"')
    return "".join(out)


# ---------------------------------------------------------------------------
# Основной рекурсивный сериализатор
# ---------------------------------------------------------------------------


def _serialize(obj: Any) -> str:
    if obj is None:
        return "null"
    if isinstance(obj, bool):  # проверка ДО int: bool — подкласс int
        return "true" if obj else "false"
    if isinstance(obj, (int, float)):
        return _format_number(obj)
    if isinstance(obj, str):
        return _encode_string(obj)
    if isinstance(obj, (list, tuple)):
        # порядок элементов массива — часть данных, не переупорядочивается
        return "[" + ",".join(_serialize(item) for item in obj) + "]"
    if isinstance(obj, dict):
        if not all(isinstance(k, str) for k in obj):
            raise CanonicalizationError("ключи JSON-объекта должны быть строками")
        sorted_keys = sorted(obj.keys(), key=_utf16_sort_key)
        members = (
            f"{_encode_string(k)}:{_serialize(obj[k])}" for k in sorted_keys
        )
        return "{" + ",".join(members) + "}"
    raise CanonicalizationError(
        f"тип {type(obj).__name__!r} не поддерживается канонизацией JSON"
    )


def canonicalize(obj: Any) -> bytes:
    """
    Возвращает каноническое представление obj по RFC 8785 (JCS) как bytes
    в кодировке UTF-8 без BOM.

    obj — уже разобранная Python-структура (результат json.loads или
    эквивалентный dict/list/str/int/float/bool/None), а не строка JSON.

    Правила см. в spec/canonicalization.md. Функция детерминирована: один и
    тот же логический объект всегда даёт один и тот же результат байт в байт,
    независимо от порядка ключей во входном dict.
    """
    return _serialize(obj).encode("utf-8")


def digest(obj: Any) -> str:
    """Возвращает SHA-256(canonicalize(obj)) в виде нижнего регистра hex-строки."""
    return hashlib.sha256(canonicalize(obj)).hexdigest()


if __name__ == "__main__":  # pragma: no cover - ручная проверка
    import json
    import sys

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        parsed = json.loads(line)
        print(canonicalize(parsed).decode("utf-8"))
        print(digest(parsed))

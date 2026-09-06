#!/usr/bin/env python3
"""cm_encoder.py — BizDNAi Circle Mark (CM) Encoder Library & CLI.

Реализует:
  * §6  логический payload (Magic/Family, CM Version, Encoding Profile,
        Namespace Hint, Entity ID, Resolver Hint, Type Hint, Integrity,
        Reserved) из CM_Encoder_Scanner_Decoder_v0.1.txt;
  * §8  грамматику Sector State (00 EMPTY / 01 INNER / 10 OUTER / 11 FULL);
  * §9  межкольцевое кодирование / interleaving.

СТАТУС: инженерный прототип v0.1 ("Phase 1 — Encoder v0.1" из §42
спецификации). Все числовые параметры геометрии Encoding Profile (число
колец, секторов, бит/сектор, ширина Reserved, параметры interleaver'а)
являются ПРОЕКТНЫМИ (design placeholders) и подлежат пересмотру после
camera/print benchmark — см. §43 "Что нельзя фиксировать до benchmark" и
§9 визуальной спецификации ("Предварительная гипотеза... не утверждённый
стандарт"). Ничего из перечисленного здесь не является CM Encoding
Profile 1.0.

В payload НЕ помещаются изменяемые бизнес-атрибуты (владелец, цена, ATI,
местоположение, статус, срок годности, роль и т.п.) — это прямой MUST NOT
раздела 6 спецификации и раздела 16 визуальной спецификации. Единственная
переменная часть — Entity ID и служебные hint-поля.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from typing import Dict, List


# ---------------------------------------------------------------------------
# Integrity — CRC-16/CCITT-FALSE
# ---------------------------------------------------------------------------
# Выбор алгоритма: CRC-16/CCITT-FALSE (poly=0x1021, init=0xFFFF, без
# reflect-in/reflect-out и без final xor).
#
# Почему именно он, а не CRC-32:
#   - раздел 6 требует MUST поле Integrity, но не фиксирует алгоритм —
#     раздел 16 явно оставляет ECC/integrity suite открытым для будущей
#     версии ("Crypto/ECC algorithms MUST быть versioned и replaceable");
#   - CRC-16 вдвое дешевле по битовому бюджету, чем CRC-32, а payload и так
#     тесный (128 бит только под Entity ID) — экономия ушла в Reserved;
#   - CCITT-FALSE — широко задокументированный вариант с открытым тестовым
#     вектором ("123456789" -> 0x29B1), что даёт независимую проверку
#     корректности реализации без сторонних библиотек (см.
#     test_cm_roundtrip.py).
# Это НЕ финальный крипто/ECC suite стандарта — только MUST-минимум для
# обнаружения повреждения payload в v0.1.

CRC16_POLY = 0x1021
CRC16_INIT = 0xFFFF


def crc16_ccitt_false_bytes(data: bytes) -> int:
    """Побайтовая реализация CRC-16/CCITT-FALSE.

    Используется только для самопроверки реализации на известном тестовом
    векторе — сам payload не выровнен по границе байта, поэтому кодирование
    payload использует битовую версию ниже.
    """
    crc = CRC16_INIT
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def crc16_ccitt_false_bits(bits: str) -> int:
    """Побитовая (bit-serial) реализация CRC-16/CCITT-FALSE.

    Математически эквивалентна побайтовой версии при подаче бит по 8 за
    такт (MSB первым) — это стандартный bit-serial CRC. Здесь нужна, т.к.
    поля payload перед CRC суммарно дают 180 бит (не кратно 8), а
    спецификация не требует байтового выравнивания полей.
    """
    crc = CRC16_INIT
    for ch in bits:
        bit = 1 if ch == "1" else 0
        msb = (crc >> 15) & 1
        crc = (crc << 1) & 0xFFFF
        if msb ^ bit:
            crc ^= CRC16_POLY
    return crc


# ---------------------------------------------------------------------------
# Sector State grammar — §8
# ---------------------------------------------------------------------------
STATE_EMPTY = 0  # 00 — пустой сектор
STATE_INNER = 1  # 01 — штрих во внутренней половине радиального диапазона
STATE_OUTER = 2  # 10 — штрих во внешней половине радиального диапазона
STATE_FULL = 3   # 11 — штрих на весь допустимый радиальный диапазон

STATE_NAMES = {STATE_EMPTY: "EMPTY", STATE_INNER: "INNER",
               STATE_OUTER: "OUTER", STATE_FULL: "FULL"}


# ---------------------------------------------------------------------------
# Encoding Profile — ПРОЕКТНЫЕ параметры (draft, не финал)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EncodingProfile:
    """Параметры одного Encoding Profile.

    Значения по умолчанию описывают профиль "cm-v0.1-draft-a" (profile_id=1)
    — первый рабочий прототип для Phase 1. Это ПРОЕКТНЫЙ выбор:

      * 4 кольца данных x 32 сектора x 2 бита/сектор = 256 бит ёмкости —
        число колец/секторов/бит-на-сектор прямо перечислено в §43 как
        "нельзя фиксировать до benchmark";
      * ширина каждого служебного поля (namespace hint 8 бит, resolver hint
        8 бит, type hint 4 бита и т.д.) выбрана "с запасом" под таблицы
        значений, а не выведена из анализа реальной ёмкости;
      * ширина Reserved вычисляется автоматически как остаток ёмкости после
        фиксированных полей (256 − 180 = 76 бит) — так и остаётся до
        Profile Freeze (Phase 5, §42);
      * параметры interleaver'а (8x16 блочная матрица) — простейшая
        детерминированная схема, удовлетворяющая §9 ("разносить соседние
        логические биты по физически удалённым позициям"), не более того.
    """

    profile_id: int = 1
    version: int = 0

    # 8-битная сигнатура семейства Circle Mark. Значение произвольное, но
    # фиксированное — служит для отличения потока CM от случайного шума при
    # bootstrap-детекции (§10 CM Core и обнаружение семейства).
    magic: int = 0xC3

    # --- ширины полей payload, бит (канонический порядок §19) ---
    magic_bits: int = 8
    version_bits: int = 4
    profile_bits: int = 4
    namespace_bits: int = 8
    entity_id_bits: int = 128
    resolver_hint_bits: int = 8
    type_hint_bits: int = 4
    crc_bits: int = 16

    # --- геометрия кодовой области (полярная матрица CM[r, θ]) ---
    sectors_per_ring: int = 32
    data_rings: int = 4
    bits_per_sector: int = 2

    # --- блочный interleaver: rows x cols должно равняться числу символов
    #     (data_rings * sectors_per_ring) ---
    interleave_rows: int = 8
    interleave_cols: int = 16

    @property
    def capacity_bits(self) -> int:
        return self.data_rings * self.sectors_per_ring * self.bits_per_sector

    @property
    def symbol_count(self) -> int:
        return self.data_rings * self.sectors_per_ring

    @property
    def fixed_fields_bits(self) -> int:
        return (
            self.magic_bits + self.version_bits + self.profile_bits +
            self.namespace_bits + self.entity_id_bits +
            self.resolver_hint_bits + self.type_hint_bits + self.crc_bits
        )

    @property
    def reserved_bits(self) -> int:
        remainder = self.capacity_bits - self.fixed_fields_bits
        if remainder < 0:
            raise ValueError(
                "Encoding profile capacity_bits (%d) меньше суммы "
                "обязательных полей (%d) — профиль некорректен" %
                (self.capacity_bits, self.fixed_fields_bits)
            )
        return remainder

    def __post_init__(self) -> None:
        if self.interleave_rows * self.interleave_cols != self.symbol_count:
            raise ValueError(
                "interleave_rows * interleave_cols (%d) должно равняться "
                "числу символов data_rings * sectors_per_ring (%d)" %
                (self.interleave_rows * self.interleave_cols, self.symbol_count)
            )
        if self.bits_per_sector != 2:
            raise ValueError(
                "Текущая реализация грамматики §8 жёстко рассчитана на "
                "2 бита/сектор (4 состояния EMPTY/INNER/OUTER/FULL)"
            )


DEFAULT_PROFILE = EncodingProfile()


# ---------------------------------------------------------------------------
# Orientation / Sync markers — §11 (профильная, не incoded-payload часть)
# ---------------------------------------------------------------------------
# Четыре крупные асимметричные marker positions с разными постоянными
# геометрическими состояниями (§11, §9 визуальной спецификации). Не несут
# Entity ID — это система координат, а не данные. base_angle_deg — угол
# "передней" (против часовой стрелки) кромки маркера в системе координат
# рендерера (0° = "12 часов", по часовой стрелке); width_deg — угловая
# ширина; shape — одна из четырёх РАЗНЫХ форм (не 3 квадрата QR/DataMatrix,
# см. §8 визуальной спецификации "Запрещённые визуальные элементы").
# Различная ширина дополнительно снимает 4-кратную неоднозначность поворота.
ORIENTATION_MARKERS = [
    {"base_angle_deg": 0,   "width_deg": 50, "shape": "SOLID"},
    {"base_angle_deg": 90,  "width_deg": 40, "shape": "OUTER_HALF"},
    {"base_angle_deg": 180, "width_deg": 30, "shape": "INNER_HALF"},
    {"base_angle_deg": 270, "width_deg": 20, "shape": "SPLIT"},
]


# ---------------------------------------------------------------------------
# Namespace / Type / Resolver hint tables (компактные коды, §6)
# ---------------------------------------------------------------------------
# "Namespace Hint — компактное указание пространства идентичности" — по
# спецификации это НЕ полная строка домена, а компактный код. Таблица ниже —
# минимальный рабочий реестр v0.1, расширяемый в будущих версиях профиля.
NAMESPACE_TABLE: Dict[str, int] = {
    "unspecified": 0,
    "bizdnai": 1,
}
NAMESPACE_TABLE_REV = {v: k for k, v in NAMESPACE_TABLE.items()}

# "Type Hint — грубый неизменяемый/стабильный class hint" (MAY). Значения —
# грубые классы объектов из §18 визуальной спецификации ("визуальная
# универсальность"), НЕ бизнес-статус и не текущая роль.
TYPE_HINT_TABLE: Dict[str, int] = {
    "unspecified": 0,
    "product": 1,
    "batch": 2,
    "document": 3,
    "asset": 4,
    "vehicle": 5,
    "building": 6,
    "badge": 7,
    "device": 8,
    "material": 9,
}
TYPE_HINT_TABLE_REV = {v: k for k, v in TYPE_HINT_TABLE.items()}

# "Resolver Hint — маршрут разрешения ID без жёсткой привязки к одному
# домену" (SHOULD). §21: "Resolver endpoint не должен быть единственной
# неизменяемой строкой, навечно зашитой в каждую метку" — поэтому здесь
# компактный код маршрутизации, а не URL/домен.
RESOLVER_HINT_TABLE: Dict[str, int] = {
    "default": 0,          # маршрутизация через глобальный BizDNAi resolver policy
    "public-registry": 1,  # публичный реестр записей (сейчас — репозиторий на GitHub)
}
RESOLVER_HINT_TABLE_REV = {v: k for k, v in RESOLVER_HINT_TABLE.items()}


# ---------------------------------------------------------------------------
# Битовые утилиты
# ---------------------------------------------------------------------------
def int_to_bits(value: int, width: int) -> str:
    if value < 0 or value >= (1 << width):
        raise ValueError(f"значение {value} не помещается в {width} бит")
    return format(value, f"0{width}b")


def bits_to_int(bits: str) -> int:
    return int(bits, 2) if bits else 0


# ---------------------------------------------------------------------------
# §6 — сборка логического payload
# ---------------------------------------------------------------------------
def encode_payload(
    entity_id: str,
    *,
    namespace: str = "bizdnai",
    type_hint: str = "unspecified",
    resolver_hint: str = "default",
    profile: EncodingProfile = DEFAULT_PROFILE,
) -> str:
    """Собирает логический payload по §6 и возвращает битовую строку
    длиной ровно profile.capacity_bits.

    Канонический порядок полей (§19 "Encoder MUST применять canonical
    field ordering/serialization"):
        Magic(8) . Version(4) . Profile(4) . Namespace(8) .
        EntityID(128) . ResolverHint(8) . TypeHint(4) . Reserved(76) .
        CRC16(16)

    CRC вычисляется над всеми предыдущими полями (Magic..Reserved).
    Encoder детерминирован (§19): одинаковый вход + profile всегда дают
    одинаковую битовую строку.
    """
    try:
        entity_uuid = uuid.UUID(str(entity_id))
    except ValueError as exc:
        raise ValueError(f"entity_id должен быть валидным UUID: {entity_id!r}") from exc
    entity_int = entity_uuid.int
    if entity_int.bit_length() > profile.entity_id_bits:
        raise ValueError("entity_id превышает ширину поля Entity ID профиля")

    if namespace not in NAMESPACE_TABLE:
        raise ValueError(
            f"неизвестный namespace {namespace!r}; допустимые: "
            f"{sorted(NAMESPACE_TABLE)}"
        )
    if type_hint not in TYPE_HINT_TABLE:
        raise ValueError(
            f"неизвестный type_hint {type_hint!r}; допустимые: "
            f"{sorted(TYPE_HINT_TABLE)}"
        )
    if resolver_hint not in RESOLVER_HINT_TABLE:
        raise ValueError(
            f"неизвестный resolver_hint {resolver_hint!r}; допустимые: "
            f"{sorted(RESOLVER_HINT_TABLE)}"
        )

    namespace_code = NAMESPACE_TABLE[namespace]
    type_code = TYPE_HINT_TABLE[type_hint]
    resolver_code = RESOLVER_HINT_TABLE[resolver_hint]

    fields_wo_crc = (
        int_to_bits(profile.magic, profile.magic_bits) +
        int_to_bits(profile.version, profile.version_bits) +
        int_to_bits(profile.profile_id, profile.profile_bits) +
        int_to_bits(namespace_code, profile.namespace_bits) +
        int_to_bits(entity_int, profile.entity_id_bits) +
        int_to_bits(resolver_code, profile.resolver_hint_bits) +
        int_to_bits(type_code, profile.type_hint_bits) +
        int_to_bits(0, profile.reserved_bits)  # Reserved — зарезервировано, v0.1 = 0
    )

    crc = crc16_ccitt_false_bits(fields_wo_crc)
    full_bits = fields_wo_crc + int_to_bits(crc, profile.crc_bits)

    if len(full_bits) != profile.capacity_bits:
        raise AssertionError(
            "внутренняя ошибка: длина payload (%d) != ёмкости профиля (%d)"
            % (len(full_bits), profile.capacity_bits)
        )
    return full_bits


def decode_payload(bits: str, profile: EncodingProfile = DEFAULT_PROFILE) -> Dict:
    """Обратная операция к encode_payload: разбирает битовую строку на поля
    и проверяет CRC. Не требует sectors/interleaving — работает с уже
    "распрямлённым" логическим payload.
    """
    if len(bits) != profile.capacity_bits:
        raise ValueError(
            f"длина bits ({len(bits)}) != ёмкости профиля ({profile.capacity_bits})"
        )

    pos = 0

    def take(width: int) -> str:
        nonlocal pos
        chunk = bits[pos:pos + width]
        pos += width
        return chunk

    magic_b = take(profile.magic_bits)
    version_b = take(profile.version_bits)
    profile_b = take(profile.profile_bits)
    namespace_b = take(profile.namespace_bits)
    entity_b = take(profile.entity_id_bits)
    resolver_b = take(profile.resolver_hint_bits)
    type_b = take(profile.type_hint_bits)
    reserved_b = take(profile.reserved_bits)
    crc_b = take(profile.crc_bits)
    assert pos == profile.capacity_bits

    fields_wo_crc = bits[: profile.capacity_bits - profile.crc_bits]
    expected_crc = crc16_ccitt_false_bits(fields_wo_crc)
    actual_crc = bits_to_int(crc_b)

    magic = bits_to_int(magic_b)
    entity_int = bits_to_int(entity_b)
    namespace_code = bits_to_int(namespace_b)
    resolver_code = bits_to_int(resolver_b)
    type_code = bits_to_int(type_b)

    return {
        "magic": magic,
        "magic_valid": magic == profile.magic,
        "version": bits_to_int(version_b),
        "profile_id": bits_to_int(profile_b),
        "namespace": NAMESPACE_TABLE_REV.get(namespace_code, f"reserved:{namespace_code}"),
        "namespace_code": namespace_code,
        "entity_id": str(uuid.UUID(int=entity_int)),
        "resolver_hint": RESOLVER_HINT_TABLE_REV.get(resolver_code, f"reserved:{resolver_code}"),
        "resolver_hint_code": resolver_code,
        "type_hint": TYPE_HINT_TABLE_REV.get(type_code, f"reserved:{type_code}"),
        "type_hint_code": type_code,
        "reserved_bits": reserved_b,
        "crc": actual_crc,
        "crc_expected": expected_crc,
        "integrity_ok": actual_crc == expected_crc and magic == profile.magic,
    }


# ---------------------------------------------------------------------------
# §9 — Interleaving
# ---------------------------------------------------------------------------
# Простой детерминированный блочный interleaver: логические 2-битные символы
# (0..symbol_count-1) записываются построчно в матрицу interleave_rows x
# interleave_cols; физический порядок ячеек — чтение по столбцам. Соседние
# логические символы (одна строка исходной матрицы, соседние столбцы)
# оказываются на расстоянии interleave_rows физических ячеек друг от друга,
# то есть локальное повреждение группы физически соседних секторов
# затрагивает символы, которые в исходном логическом потоке были далеко
# друг от друга — а не один непрерывный кусок payload (§9 требование).
def _interleave_physical_to_logical(profile: EncodingProfile) -> List[int]:
    rows, cols = profile.interleave_rows, profile.interleave_cols
    mapping = [0] * (rows * cols)
    p = 0
    for col in range(cols):
        for row in range(rows):
            mapping[p] = row * cols + col
            p += 1
    return mapping


# ---------------------------------------------------------------------------
# §7/§8 — раскладка в полярную матрицу CM[r, θ]
# ---------------------------------------------------------------------------
def to_sectors(bits: str, profile: EncodingProfile = DEFAULT_PROFILE) -> Dict:
    """Раскладывает битовую строку payload в полярную матрицу секторов.

    Возвращает {"data": [[state,...]*sectors_per_ring]*data_rings,
                "parity": [state,...]*sectors_per_ring}

    data[ring][sector] — состояние (0..3) кольца `ring` (0 = самое
    внутреннее кольцо данных) в угловой позиции `sector`.

    parity — дополнительное физическое "ECC/Integrity" кольцо (§9
    "вертикальный набор состояний... может формировать parity/codeword"):
    для каждой угловой позиции θ это XOR состояний всех 4 колец данных в
    этой позиции. Это НЕ полноценный ECC (Reed-Solomon/BCH и т.п. по §16
    ещё не выбраны) — только простая межкольцевая parity-подсказка,
    позволяющая decoder'у заметить несогласованность конкретного столбца.
    """
    if len(bits) != profile.capacity_bits:
        raise ValueError(
            f"длина bits ({len(bits)}) != ёмкости профиля ({profile.capacity_bits})"
        )
    n = profile.symbol_count
    logical_symbols = [
        bits_to_int(bits[2 * i:2 * i + 2]) for i in range(n)
    ]

    phys_to_log = _interleave_physical_to_logical(profile)
    data = [[STATE_EMPTY] * profile.sectors_per_ring for _ in range(profile.data_rings)]
    for p in range(n):
        s = phys_to_log[p]
        symbol = logical_symbols[s]
        ring = p // profile.sectors_per_ring
        sector = p % profile.sectors_per_ring
        data[ring][sector] = symbol

    parity = [STATE_EMPTY] * profile.sectors_per_ring
    for sector in range(profile.sectors_per_ring):
        x = 0
        for ring in range(profile.data_rings):
            x ^= data[ring][sector]
        parity[sector] = x

    return {"data": data, "parity": parity}


def decode_sectors(sectors: Dict, profile: EncodingProfile = DEFAULT_PROFILE) -> Dict:
    """Обратная операция к to_sectors: восстанавливает payload из полярной
    матрицы секторов и, дополнительно к decode_payload, отмечает угловые
    позиции, где межкольцевая parity не сходится (возможное повреждение).
    """
    data = sectors["data"]
    parity = sectors.get("parity")
    n = profile.symbol_count

    phys_to_log = _interleave_physical_to_logical(profile)
    logical_symbols = [0] * n
    for p in range(n):
        ring = p // profile.sectors_per_ring
        sector = p % profile.sectors_per_ring
        s = phys_to_log[p]
        logical_symbols[s] = data[ring][sector]

    bits = "".join(int_to_bits(sym, 2) for sym in logical_symbols)

    parity_mismatches: List[int] = []
    if parity is not None:
        for sector in range(profile.sectors_per_ring):
            x = 0
            for ring in range(profile.data_rings):
                x ^= data[ring][sector]
            if x != parity[sector]:
                parity_mismatches.append(sector)

    result = decode_payload(bits, profile)
    result["bits"] = bits
    result["parity_mismatches"] = parity_mismatches
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="BizDNAi Circle Mark (CM) — encoder CLI (draft v0.1, "
                    "профиль cm-v0.1-draft-a, только stdlib)."
    )
    p.add_argument("--entity-id", required=True, help="Entity ID (UUID)")
    p.add_argument("--namespace", default="bizdnai", choices=sorted(NAMESPACE_TABLE))
    p.add_argument("--type-hint", default="unspecified", choices=sorted(TYPE_HINT_TABLE))
    p.add_argument("--resolver-hint", default="default", choices=sorted(RESOLVER_HINT_TABLE))
    p.add_argument("--json", action="store_true", help="вывести sectors как JSON")
    return p


def main(argv=None) -> int:
    args = _build_arg_parser().parse_args(argv)
    profile = DEFAULT_PROFILE

    bits = encode_payload(
        args.entity_id,
        namespace=args.namespace,
        type_hint=args.type_hint,
        resolver_hint=args.resolver_hint,
        profile=profile,
    )
    sectors = to_sectors(bits, profile)
    decoded = decode_sectors(sectors, profile)

    print("Профиль:", profile.profile_id, "версия:", profile.version, "(ПРОЕКТНЫЙ, не финал)")
    print("Ёмкость:", profile.capacity_bits, "бит  |  payload:", len(bits), "бит  |  reserved:", profile.reserved_bits, "бит")
    print("Payload bits:", bits)
    print("Round-trip decode:", json.dumps(decoded, ensure_ascii=False, indent=2))

    if args.json:
        print(json.dumps(sectors, ensure_ascii=False))

    return 0 if decoded["integrity_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

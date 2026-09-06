#!/usr/bin/env python3
"""test_cm_roundtrip.py — самопроверка CM Encoder/Renderer (draft v0.1).

Только стандартная библиотека (unittest). Проверяет:
  1. CRC-16/CCITT-FALSE реализация (байтовая и битовая) совпадает с
     общеизвестным тестовым вектором и друг с другом.
  2. encode_payload -> to_sectors -> decode_sectors возвращает исходный
     payload (round-trip) для нескольких Entity ID, включая границы
     (all-zero UUID, all-one UUID) и разные namespace/type/resolver hints.
  3. Повреждение случайных секторов обнаруживается через integrity check
     (CRC) и/или межкольцевую parity.
  4. render_svg производит валидный XML, парсящийся стандартным
     xml.dom.minidom, для нескольких payload.

Запуск: python3 test_cm_roundtrip.py -v
"""

from __future__ import annotations

import os
import random
import sys
import unittest
import uuid
import xml.dom.minidom as minidom

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cm_encoder import (  # noqa: E402
    DEFAULT_PROFILE,
    STATE_EMPTY,
    STATE_FULL,
    crc16_ccitt_false_bits,
    crc16_ccitt_false_bytes,
    decode_sectors,
    encode_payload,
    to_sectors,
)
from cm_render import render_svg  # noqa: E402


class Crc16Tests(unittest.TestCase):
    def test_known_vector_bytes(self):
        # CRC-16/CCITT-FALSE("123456789") == 0x29B1 — общеизвестный
        # тестовый вектор (catalogue of CRC algorithms).
        self.assertEqual(crc16_ccitt_false_bytes(b"123456789"), 0x29B1)

    def test_bit_serial_matches_byte_wise(self):
        data = b"123456789"
        bits = "".join(format(b, "08b") for b in data)
        self.assertEqual(crc16_ccitt_false_bits(bits), 0x29B1)
        self.assertEqual(crc16_ccitt_false_bits(bits), crc16_ccitt_false_bytes(data))

    def test_bit_serial_matches_byte_wise_random(self):
        rng = random.Random(42)
        for _ in range(20):
            data = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 40)))
            bits = "".join(format(b, "08b") for b in data)
            self.assertEqual(crc16_ccitt_false_bits(bits), crc16_ccitt_false_bytes(data))


ENTITY_IDS = [
    "0199a3f7-8c21-7d4e-b6a9-2f5e8c1d4a70",  # genesis mark ID
    str(uuid.UUID(int=0)),                    # all-zero (граничный случай)
    str(uuid.UUID(int=(1 << 128) - 1)),       # all-one (граничный случай)
    str(uuid.uuid4()),
    str(uuid.uuid4()),
]


class RoundTripTests(unittest.TestCase):
    def test_capacity_matches_expectations(self):
        p = DEFAULT_PROFILE
        self.assertEqual(p.capacity_bits, 256)
        self.assertEqual(p.fixed_fields_bits, 180)
        self.assertEqual(p.reserved_bits, 76)
        self.assertEqual(p.symbol_count, 128)

    def test_encode_is_deterministic(self):
        bits1 = encode_payload(ENTITY_IDS[0], namespace="bizdnai", type_hint="document")
        bits2 = encode_payload(ENTITY_IDS[0], namespace="bizdnai", type_hint="document")
        self.assertEqual(bits1, bits2)

    def test_roundtrip_all_entity_ids(self):
        for entity_id in ENTITY_IDS:
            for type_hint, resolver_hint in (
                ("document", "default"),
                ("unspecified", "default"),
                ("product", "default"),
            ):
                with self.subTest(entity_id=entity_id, type_hint=type_hint):
                    bits = encode_payload(
                        entity_id, namespace="bizdnai",
                        type_hint=type_hint, resolver_hint=resolver_hint,
                    )
                    self.assertEqual(len(bits), DEFAULT_PROFILE.capacity_bits)
                    sectors = to_sectors(bits, DEFAULT_PROFILE)
                    decoded = decode_sectors(sectors, DEFAULT_PROFILE)
                    self.assertTrue(decoded["integrity_ok"])
                    self.assertEqual(decoded["entity_id"], entity_id)
                    self.assertEqual(decoded["namespace"], "bizdnai")
                    self.assertEqual(decoded["type_hint"], type_hint)
                    self.assertEqual(decoded["resolver_hint"], resolver_hint)
                    self.assertEqual(decoded["bits"], bits)
                    self.assertEqual(decoded["parity_mismatches"], [])

    def test_sector_states_are_valid_grammar(self):
        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)
        for ring in sectors["data"]:
            for state in ring:
                self.assertIn(state, (0, 1, 2, 3))
        for state in sectors["parity"]:
            self.assertIn(state, (0, 1, 2, 3))

    def test_interleaving_disperses_neighbours(self):
        """Проверка §9: два логически соседних символа (payload) не должны
        оказываться в физически соседних ячейках одного кольца."""
        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)
        # Найдём физическую ячейку (ring, sector) для логического символа 0
        # и логического символа 1, и убедимся, что они не соседи.
        from cm_encoder import _interleave_physical_to_logical
        phys_to_log = _interleave_physical_to_logical(DEFAULT_PROFILE)
        log_to_phys = {s: p for p, s in enumerate(phys_to_log)}
        p0, p1 = log_to_phys[0], log_to_phys[1]
        self.assertNotEqual(abs(p0 - p1), 1,
                             "соседние логические символы не должны быть физически соседними")


class DamageDetectionTests(unittest.TestCase):
    def test_random_sector_corruption_detected_by_integrity(self):
        rng = random.Random(7)
        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)

        detected = 0
        trials = 30
        for _ in range(trials):
            corrupted = {
                "data": [row[:] for row in sectors["data"]],
                "parity": sectors["parity"][:],
            }
            # повредить несколько случайных секторов данных
            n_corrupt = rng.randint(3, 8)
            for _ in range(n_corrupt):
                ring = rng.randrange(DEFAULT_PROFILE.data_rings)
                sector = rng.randrange(DEFAULT_PROFILE.sectors_per_ring)
                old = corrupted["data"][ring][sector]
                new = rng.choice([s for s in (STATE_EMPTY, 1, 2, STATE_FULL) if s != old])
                corrupted["data"][ring][sector] = new

            decoded = decode_sectors(corrupted, DEFAULT_PROFILE)
            if (not decoded["integrity_ok"]) or decoded["parity_mismatches"]:
                detected += 1

        # При случайном повреждении нескольких секторов подавляющее
        # большинство случаев должно быть обнаружено CRC-16 и/или parity.
        # (Гарантированное 100% восстановление НЕ заявляется — полноценный
        # ECC по §16 ещё не выбран; здесь только детект повреждения.)
        self.assertGreaterEqual(detected, trials * 0.9,
                                 f"обнаружено только {detected}/{trials} повреждений")

    def test_single_column_flip_detected_by_parity(self):
        """Изменение одного кольца в одной угловой позиции почти всегда
        должно нарушать межкольцевую parity в этом столбце."""
        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)
        corrupted = {
            "data": [row[:] for row in sectors["data"]],
            "parity": sectors["parity"][:],
        }
        ring, sector = 2, 10
        old = corrupted["data"][ring][sector]
        new = (old + 1) % 4
        corrupted["data"][ring][sector] = new
        decoded = decode_sectors(corrupted, DEFAULT_PROFILE)
        self.assertIn(sector, decoded["parity_mismatches"])


class RenderTests(unittest.TestCase):
    def test_svg_is_valid_xml(self):
        for entity_id in ENTITY_IDS[:3]:
            bits = encode_payload(entity_id, type_hint="document")
            sectors = to_sectors(bits, DEFAULT_PROFILE)
            svg = render_svg(sectors, profile=DEFAULT_PROFILE)
            self.assertTrue(svg.strip().startswith("<svg"))
            dom = minidom.parseString(svg)
            self.assertEqual(dom.documentElement.tagName, "svg")

    def test_svg_accent_core_still_valid(self):
        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)
        svg = render_svg(sectors, profile=DEFAULT_PROFILE, accent_core=True)
        dom = minidom.parseString(svg)
        self.assertEqual(dom.documentElement.tagName, "svg")
        self.assertIn("#4800FF", svg)

    def test_border_ring_geometry_within_required_ranges(self):
        """Задача владельца: рамка r в 470-478, толщина 5-7, зазор до
        Parity-кольца >=40, зазор до края канвы >=15."""
        from cm_render import BORDER_IN_R, BORDER_OUT_R, BORDER_THICKNESS, PARITY_OUT_R, QUIET_ZONE_OUT_R
        self.assertGreaterEqual(BORDER_IN_R, 470)
        self.assertLessEqual(BORDER_OUT_R, 478)
        self.assertGreaterEqual(BORDER_THICKNESS, 5)
        self.assertLessEqual(BORDER_THICKNESS, 7)
        self.assertGreaterEqual(BORDER_IN_R - PARITY_OUT_R, 40)
        self.assertGreaterEqual(QUIET_ZONE_OUT_R - BORDER_OUT_R, 15)

    def test_svg_with_center_logo_is_valid_and_embeds_data_uri(self):
        logo_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "assets", "bizdnai-logo.png"
        )
        if not os.path.exists(logo_path):
            self.skipTest(f"логотип не найден: {logo_path}")

        bits = encode_payload(ENTITY_IDS[0], type_hint="document")
        sectors = to_sectors(bits, DEFAULT_PROFILE)
        svg = render_svg(sectors, profile=DEFAULT_PROFILE, center_logo_path=logo_path)

        dom = minidom.parseString(svg)
        self.assertEqual(dom.documentElement.tagName, "svg")

        images = dom.getElementsByTagName("image")
        self.assertEqual(len(images), 1)
        href = images[0].getAttribute("href")
        self.assertTrue(href.startswith("data:image/png;base64,"),
                         "логотип обязан быть встроен как data URI, без внешних ссылок")
        self.assertEqual(images[0].getAttribute("xlink:href"), href)

        # логотип заменяет точку CORE_DOT_R, а не накладывается поверх неё
        no_logo_svg = render_svg(sectors, profile=DEFAULT_PROFILE)
        self.assertEqual(len(minidom.parseString(no_logo_svg).getElementsByTagName("image")), 0)

    def test_center_logo_defaults_to_disabled(self):
        import inspect
        sig = inspect.signature(render_svg)
        self.assertIsNone(sig.parameters["center_logo_path"].default)


if __name__ == "__main__":
    unittest.main(verbosity=2)

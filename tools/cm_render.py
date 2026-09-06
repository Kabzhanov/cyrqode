#!/usr/bin/env python3
"""cm_render.py — BizDNAi Circle Mark (CM) vector (SVG) renderer.

Реализует §7 (полярная геометрия), §10 (CM Core), §11 (Sync/Orientation)
CM_Encoder_Scanner_Decoder_v0.1.txt, и раздел про запрещённые/обязательные
визуальные элементы BizDNAi_Circle_Mark_Visual_Specification_v0.1.txt.

СТАТУС: как и cm_encoder.py — инженерный прототип v0.1. Все радиусы,
толщины колец и ширины зазоров ниже — КОНКРЕТНЫЙ выбор для первого
воспроизводимого рендера, а не финальная геометрия печати/гравировки.
Финальные размеры, quiet zone, stroke tolerances и DPI/материал профили
утверждаются отдельным документом "CM Geometry & Print Specification
v0.1" после camera/print benchmark (§46 encoder-спецификации).

Вывод — валидный самодостаточный SVG (viewBox 0 0 1000 1000), без внешних
зависимостей (шрифтов, картинок, скриптов). Монохромный профиль
обязателен для всех элементов, несущих информацию (§20 "Renderer MUST
иметь... monochrome-first output"); фирменный цвет допускается только для
CM Core, который не несёт уникальный Entity ID (§10).
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import xml.sax.saxutils as saxutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cm_encoder import (  # noqa: E402
    DEFAULT_PROFILE,
    ORIENTATION_MARKERS,
    STATE_EMPTY,
    STATE_FULL,
    STATE_INNER,
    STATE_OUTER,
    EncodingProfile,
    NAMESPACE_TABLE,
    RESOLVER_HINT_TABLE,
    TYPE_HINT_TABLE,
    encode_payload,
    to_sectors,
)

# ---------------------------------------------------------------------------
# Геометрия — конкретные (не финальные) радиусы, viewBox 1000x1000, центр
# (500,500). Строятся цепочкой от центра наружу, так что смена одного числа
# не рассогласовывает остальные.
# ---------------------------------------------------------------------------
CANVAS = 1000
CENTER = CANVAS / 2  # 500

# CM Core (§10, §3 визуальной спецификации): постоянный "bullseye" —
# закрашенный диск, белое кольцо-вырез, закрашенная точка в центре.
# Символ одинаков для всех меток и не несёт Entity ID.
CORE_DOT_R = 65
CORE_WHITE_R = 130
CORE_OUT_R = 176

SYNC_GAP = 14          # зазор между Core и Sync/Orientation кольцом
SYNC_IN_R = CORE_OUT_R + SYNC_GAP          # 190
SYNC_THICKNESS = 44
SYNC_OUT_R = SYNC_IN_R + SYNC_THICKNESS    # 234

DATA_GAP = 12          # зазор между Sync-кольцом и первым кольцом данных
RING_GAP = 6           # зазор между соседними кольцами данных/parity
RING_THICKNESS = 30

DATA0_IN_R = SYNC_OUT_R + DATA_GAP         # 246
DATA0_OUT_R = DATA0_IN_R + RING_THICKNESS  # 276
DATA1_IN_R = DATA0_OUT_R + RING_GAP        # 282
DATA1_OUT_R = DATA1_IN_R + RING_THICKNESS  # 312
DATA2_IN_R = DATA1_OUT_R + RING_GAP        # 318
DATA2_OUT_R = DATA2_IN_R + RING_THICKNESS  # 348
DATA3_IN_R = DATA2_OUT_R + RING_GAP        # 354
DATA3_OUT_R = DATA3_IN_R + RING_THICKNESS  # 384

PARITY_IN_R = DATA3_OUT_R + RING_GAP       # 390
PARITY_OUT_R = PARITY_IN_R + RING_THICKNESS  # 420

# Кольца данных упорядочены от центра наружу: ring 0 (внутреннее, ближе к
# Sync) -> ring 3 (внешнее, ближе к Parity). Это соответствует Progressive
# Resolution (§10 визуальной спецификации): на средней дистанции читаемы
# внутренние кольца (общий protocol/namespace-поток), для полного Entity ID
# и ECC нужно приблизиться и разрешить внешние кольца/parity.
DATA_RING_RADII = [
    (DATA0_IN_R, DATA0_OUT_R),
    (DATA1_IN_R, DATA1_OUT_R),
    (DATA2_IN_R, DATA2_OUT_R),
    (DATA3_IN_R, DATA3_OUT_R),
]

# Quiet zone (§7 "фиксированные... нормативные quiet/separation zones"):
# всё, что снаружи PARITY_OUT_R, остаётся пустым до края канвы. При
# PARITY_OUT_R=420 и CANVAS=1000 метка (без учёта quiet zone) занимает
# 2*420/1000 = 84% кадра — попадает в требуемый диапазон 80-85% (§20
# визуальной спецификации).
QUIET_ZONE_OUT_R = CANVAS / 2  # 500, край канвы

SECTOR_GAP_FRACTION = 0.16  # доля углового шага сектора, оставляемая пустой
                             # между соседними секторами (separation zone)


def _fmt(v: float) -> str:
    return f"{v:.3f}"


def polar_to_xy(cx: float, cy: float, r: float, angle_deg: float):
    """Угол 0° = "12 часов", возрастает по часовой стрелке (рендер-
    конвенция; не диктуется спецификацией, только фиксируется здесь)."""
    a = math.radians(angle_deg)
    return cx + r * math.sin(a), cy - r * math.cos(a)


def annular_sector_path(cx: float, cy: float, r_in: float, r_out: float,
                         a_start: float, a_end: float) -> str:
    """SVG path для кольцевого сектора (или клина, если r_in<=0) между
    углами a_start..a_end (градусы, < 180 по построению)."""
    large_arc = 1 if (a_end - a_start) > 180 else 0
    x1, y1 = polar_to_xy(cx, cy, r_out, a_start)
    x2, y2 = polar_to_xy(cx, cy, r_out, a_end)
    if r_in <= 0:
        return (
            f"M {_fmt(cx)},{_fmt(cy)} L {_fmt(x1)},{_fmt(y1)} "
            f"A {_fmt(r_out)},{_fmt(r_out)} 0 {large_arc} 1 {_fmt(x2)},{_fmt(y2)} Z"
        )
    x3, y3 = polar_to_xy(cx, cy, r_in, a_end)
    x4, y4 = polar_to_xy(cx, cy, r_in, a_start)
    return (
        f"M {_fmt(x1)},{_fmt(y1)} "
        f"A {_fmt(r_out)},{_fmt(r_out)} 0 {large_arc} 1 {_fmt(x2)},{_fmt(y2)} "
        f"L {_fmt(x3)},{_fmt(y3)} "
        f"A {_fmt(r_in)},{_fmt(r_in)} 0 {large_arc} 0 {_fmt(x4)},{_fmt(y4)} Z"
    )


def _sector_state_path(cx, cy, r_in, r_out, a_start, a_end, state: int):
    r_mid = (r_in + r_out) / 2
    if state == STATE_EMPTY:
        return None
    if state == STATE_INNER:
        return annular_sector_path(cx, cy, r_in, r_mid, a_start, a_end)
    if state == STATE_OUTER:
        return annular_sector_path(cx, cy, r_mid, r_out, a_start, a_end)
    if state == STATE_FULL:
        return annular_sector_path(cx, cy, r_in, r_out, a_start, a_end)
    raise ValueError(f"неизвестное sector state {state!r}")


def _render_data_ring(cx, cy, r_in, r_out, states, ink: str) -> str:
    n = len(states)
    step = 360.0 / n
    gap = step * SECTOR_GAP_FRACTION
    parts = []
    for i, state in enumerate(states):
        a_start = i * step + gap / 2
        a_end = (i + 1) * step - gap / 2
        d = _sector_state_path(cx, cy, r_in, r_out, a_start, a_end, state)
        if d:
            parts.append(f'<path d="{d}" fill="{ink}"/>')
    return "\n".join(parts)


def _render_orientation_ring(cx, cy, r_in, r_out, ink: str) -> str:
    """Четыре асимметричных marker positions (§11, §8-9 визуальной
    спецификации): разные по ширине и по форме, не 3-квадратный QR finder.
    """
    r_mid = (r_in + r_out) / 2
    parts = []
    for marker in ORIENTATION_MARKERS:
        a0 = marker["base_angle_deg"]
        a1 = a0 + marker["width_deg"]
        shape = marker["shape"]
        if shape == "SOLID":
            d = annular_sector_path(cx, cy, r_in, r_out, a0, a1)
            parts.append(f'<path d="{d}" fill="{ink}"/>')
        elif shape == "OUTER_HALF":
            d = annular_sector_path(cx, cy, r_mid, r_out, a0, a1)
            parts.append(f'<path d="{d}" fill="{ink}"/>')
        elif shape == "INNER_HALF":
            d = annular_sector_path(cx, cy, r_in, r_mid, a0, a1)
            parts.append(f'<path d="{d}" fill="{ink}"/>')
        elif shape == "SPLIT":
            band = (r_out - r_in) * 0.22
            d1 = annular_sector_path(cx, cy, r_in, r_in + band, a0, a1)
            d2 = annular_sector_path(cx, cy, r_out - band, r_out, a0, a1)
            parts.append(f'<path d="{d1}" fill="{ink}"/>')
            parts.append(f'<path d="{d2}" fill="{ink}"/>')
        else:
            raise ValueError(f"неизвестная форма orientation marker: {shape!r}")
    return "\n".join(parts)


def _render_core(cx, cy, ink: str, core_color: str) -> str:
    return (
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(CORE_OUT_R)}" fill="{core_color}"/>\n'
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(CORE_WHITE_R)}" fill="#FFFFFF"/>\n'
        f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(CORE_DOT_R)}" fill="{core_color}"/>'
    )


def render_svg(
    sectors: dict,
    *,
    profile: EncodingProfile = DEFAULT_PROFILE,
    ink: str = "#04101F",
    background: str = "#FFFFFF",
    accent_core: bool = False,
    accent_color: str = "#4800FF",
    title: str = "BizDNAi Circle Mark",
) -> str:
    """Рендерит sectors (см. cm_encoder.to_sectors) в самодостаточный SVG.

    Монохромный профиль обязателен для ВСЕХ элементов, несущих информацию
    (Sync/Orientation, Data rings, Parity ring) — цвет для них не
    параметризуется намеренно, чтобы decoder мог полагаться только на
    геометрию/контраст (§8 "Decoder MUST определять состояние по геометрии
    и контрасту, а не по цвету"). Фирменный цвет (accent_core) допускается
    ТОЛЬКО для CM Core, который одинаков для всех меток и не несёт Entity
    ID (§10) — это "non-essential layer, не меняющий decode" (§20).
    """
    cx = cy = CENTER
    core_color = accent_color if accent_core else ink

    data_rings_svg = []
    for ring_idx, (r_in, r_out) in enumerate(DATA_RING_RADII):
        states = sectors["data"][ring_idx]
        data_rings_svg.append(_render_data_ring(cx, cy, r_in, r_out, states, ink))

    parity_svg = _render_data_ring(cx, cy, PARITY_IN_R, PARITY_OUT_R, sectors["parity"], ink)
    sync_svg = _render_orientation_ring(cx, cy, SYNC_IN_R, SYNC_OUT_R, ink)
    core_svg = _render_core(cx, cy, ink, core_color)

    safe_title = saxutils.escape(title)

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {CANVAS} {CANVAS}" width="{CANVAS}" height="{CANVAS}">
<title>{safe_title}</title>
<desc>BizDNAi Circle Mark — draft v0.1 (engineering prototype, encoding profile cm-v0.1-draft-a). Geometry is provisional, not a final print/engraving standard.</desc>
<rect x="0" y="0" width="{CANVAS}" height="{CANVAS}" fill="{background}"/>
<g id="cm-parity-ring">
{parity_svg}
</g>
<g id="cm-data-ring-3">
{data_rings_svg[3]}
</g>
<g id="cm-data-ring-2">
{data_rings_svg[2]}
</g>
<g id="cm-data-ring-1">
{data_rings_svg[1]}
</g>
<g id="cm-data-ring-0">
{data_rings_svg[0]}
</g>
<g id="cm-sync-orientation-ring">
{sync_svg}
</g>
<g id="cm-core">
{core_svg}
</g>
</svg>
'''
    return svg


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="BizDNAi Circle Mark (CM) — SVG renderer CLI (draft v0.1)."
    )
    p.add_argument("--entity-id", required=True, help="Entity ID (UUID)")
    p.add_argument("--namespace", default="bizdnai", choices=sorted(NAMESPACE_TABLE))
    p.add_argument("--type-hint", default="unspecified", choices=sorted(TYPE_HINT_TABLE))
    p.add_argument("--resolver-hint", default="default", choices=sorted(RESOLVER_HINT_TABLE))
    p.add_argument("--out", required=True, help="путь для выходного .svg")
    p.add_argument("--accent-core", action="store_true",
                    help="закрасить CM Core фирменным фиолетовым #4800FF "
                         "(не меняет data/orientation/parity кольца — они всегда монохромны)")
    p.add_argument("--ink", default="#04101F", help="основной цвет data-элементов")
    p.add_argument("--background", default="#FFFFFF")
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
    svg = render_svg(
        sectors,
        profile=profile,
        ink=args.ink,
        background=args.background,
        accent_core=args.accent_core,
        title=f"BizDNAi Circle Mark — {args.entity_id}",
    )

    out_path = args.out
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Записан {out_path} ({len(svg)} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

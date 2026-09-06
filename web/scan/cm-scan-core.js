/*!
 * cm-scan-core.js — BizDNAi Circle Mark (CM) scanner: bit-exact codec +
 * camera-side geometry detector, v0.1 (engineering prototype).
 *
 * ЭТОТ ФАЙЛ ОБЯЗАН ТОЧНО ПОВТОРЯТЬ параметры генератора:
 *   - tools/cm_encoder.py  (payload layout, CRC-16/
 *     CCITT-FALSE, interleaving, профиль cm-v0.1-draft-a)
 *   - tools/cm_render.py   (радиусы колец, число
 *     секторов/колец, sector-state геометрия, orientation markers)
 *
 * Ничего здесь не придумано заново — все числа ниже списаны 1:1 из
 * генератора (см. комментарии у каждой константы с указанием исходной
 * python-переменной). Никаких внешних библиотек, только стандартный JS.
 *
 * Используется и в selftest.html (декодирование растеризованного
 * эталонного SVG), и в scan.js (декодирование кадра с камеры) — это
 * гарантирует, что "тот же декодер" в обоих местах буквально один и тот
 * же код, а не две параллельные реализации.
 */
(function (global) {
  "use strict";

  // =========================================================================
  // §Профиль — 1:1 из cm_encoder.py EncodingProfile (DEFAULT_PROFILE,
  // profile_id=1, "cm-v0.1-draft-a")
  // =========================================================================
  var PROFILE = {
    profile_id: 1,
    version: 0,
    magic: 0xC3, // cm_encoder.py: EncodingProfile.magic

    magic_bits: 8,
    version_bits: 4,
    profile_bits: 4,
    namespace_bits: 8,
    entity_id_bits: 128,
    resolver_hint_bits: 8,
    type_hint_bits: 4,
    crc_bits: 16,

    sectors_per_ring: 32,
    data_rings: 4,
    bits_per_sector: 2,

    interleave_rows: 8,
    interleave_cols: 16,
  };
  // capacity_bits = data_rings * sectors_per_ring * bits_per_sector = 256
  PROFILE.capacity_bits = PROFILE.data_rings * PROFILE.sectors_per_ring * PROFILE.bits_per_sector;
  PROFILE.symbol_count = PROFILE.data_rings * PROFILE.sectors_per_ring; // 128
  PROFILE.fixed_fields_bits =
    PROFILE.magic_bits + PROFILE.version_bits + PROFILE.profile_bits +
    PROFILE.namespace_bits + PROFILE.entity_id_bits +
    PROFILE.resolver_hint_bits + PROFILE.type_hint_bits + PROFILE.crc_bits;
  PROFILE.reserved_bits = PROFILE.capacity_bits - PROFILE.fixed_fields_bits; // 76

  // Reverse hint tables — 1:1 из cm_encoder.py *_TABLE
  var NAMESPACE_TABLE_REV = { 0: "unspecified", 1: "bizdnai" };
  var TYPE_HINT_TABLE_REV = {
    0: "unspecified", 1: "product", 2: "batch", 3: "document", 4: "asset",
    5: "vehicle", 6: "building", 7: "badge", 8: "device", 9: "material",
  };
  var RESOLVER_HINT_TABLE_REV = { 0: "default", 1: "public-registry" };

  // Sector state grammar — §8, cm_encoder.py STATE_*
  var STATE_EMPTY = 0, STATE_INNER = 1, STATE_OUTER = 2, STATE_FULL = 3;

  // Orientation markers — 1:1 из cm_encoder.py ORIENTATION_MARKERS
  var ORIENTATION_MARKERS = [
    { base_angle_deg: 0, width_deg: 50, shape: "SOLID" },
    { base_angle_deg: 90, width_deg: 40, shape: "OUTER_HALF" },
    { base_angle_deg: 180, width_deg: 30, shape: "INNER_HALF" },
    { base_angle_deg: 270, width_deg: 20, shape: "SPLIT" },
  ];

  // =========================================================================
  // §Геометрия — 1:1 из cm_render.py (радиусы в "design units", canvas
  // 1000x1000, центр 500,500). Комментарии справа = имя python-константы.
  // =========================================================================
  var GEO = (function () {
    var CANVAS = 1000;
    var CENTER = CANVAS / 2; // 500
    var CORE_DOT_R = 65;
    var CORE_WHITE_R = 130;
    var CORE_OUT_R = 176;
    var SYNC_GAP = 14;
    var SYNC_IN_R = CORE_OUT_R + SYNC_GAP; // 190
    var SYNC_THICKNESS = 44;
    var SYNC_OUT_R = SYNC_IN_R + SYNC_THICKNESS; // 234
    var DATA_GAP = 12;
    var RING_GAP = 6;
    var RING_THICKNESS = 30;
    var DATA0_IN_R = SYNC_OUT_R + DATA_GAP; // 246
    var DATA0_OUT_R = DATA0_IN_R + RING_THICKNESS; // 276
    var DATA1_IN_R = DATA0_OUT_R + RING_GAP; // 282
    var DATA1_OUT_R = DATA1_IN_R + RING_THICKNESS; // 312
    var DATA2_IN_R = DATA1_OUT_R + RING_GAP; // 318
    var DATA2_OUT_R = DATA2_IN_R + RING_THICKNESS; // 348
    var DATA3_IN_R = DATA2_OUT_R + RING_GAP; // 354
    var DATA3_OUT_R = DATA3_IN_R + RING_THICKNESS; // 384
    var PARITY_IN_R = DATA3_OUT_R + RING_GAP; // 390
    var PARITY_OUT_R = PARITY_IN_R + RING_THICKNESS; // 420
    // Внешняя рамка (коммит 12e119e, cm_render.py BORDER_*): замкнутая
    // окружность-обводка по краю метки, не несёт бит payload — чисто
    // геометрический ориентир для оценки масштаба/полноты кадра.
    var BORDER_GAP = 51;
    var BORDER_IN_R = PARITY_OUT_R + BORDER_GAP; // 471
    var BORDER_THICKNESS = 6;
    var BORDER_OUT_R = BORDER_IN_R + BORDER_THICKNESS; // 477
    var BORDER_MID_R = (BORDER_IN_R + BORDER_OUT_R) / 2; // 474 — cm_render.py рисует circle r=474 stroke-width=6
    // LOGO_R = CORE_WHITE_R - LOGO_INSET(3) = 127 — опциональный логотип
    // (--center-logo) занимает r=0..127 внутри белого кольца-выреза,
    // поэтому декодер НЕ должен полагаться на содержимое центра (r<130):
    // единственная гарантированно одинаковая для обоих вариантов деталь —
    // цветное кольцо ядра r=130..176 и белый зазор r=176..190 вокруг него.
    var LOGO_R = 127;
    return {
      CANVAS: CANVAS, CENTER: CENTER,
      CORE_DOT_R: CORE_DOT_R, CORE_WHITE_R: CORE_WHITE_R, CORE_OUT_R: CORE_OUT_R,
      LOGO_R: LOGO_R,
      SYNC_IN_R: SYNC_IN_R, SYNC_OUT_R: SYNC_OUT_R,
      DATA_RING_RADII: [
        [DATA0_IN_R, DATA0_OUT_R], [DATA1_IN_R, DATA1_OUT_R],
        [DATA2_IN_R, DATA2_OUT_R], [DATA3_IN_R, DATA3_OUT_R],
      ],
      PARITY_IN_R: PARITY_IN_R, PARITY_OUT_R: PARITY_OUT_R,
      BORDER_IN_R: BORDER_IN_R, BORDER_OUT_R: BORDER_OUT_R, BORDER_MID_R: BORDER_MID_R,
      BORDER_THICKNESS: BORDER_THICKNESS,
      QUIET_ZONE_OUT_R: CENTER, // 500 — край канвы, cm_render.py QUIET_ZONE_OUT_R
      SECTOR_GAP_FRACTION: 0.16, // cm_render.py SECTOR_GAP_FRACTION
    };
  })();

  // =========================================================================
  // Битовые утилиты
  // =========================================================================
  function intToBits(value, width) {
    var s = value.toString(2);
    while (s.length < width) s = "0" + s;
    return s;
  }
  function bitsToInt(bits) {
    return bits.length ? parseInt(bits, 2) : 0;
  }

  // CRC-16/CCITT-FALSE, bit-serial — 1:1 из cm_encoder.crc16_ccitt_false_bits
  // (poly=0x1021, init=0xFFFF, MSB-first, без reflect/final-xor).
  var CRC16_POLY = 0x1021;
  var CRC16_INIT = 0xFFFF;
  function crc16CcittFalseBits(bits) {
    var crc = CRC16_INIT;
    for (var i = 0; i < bits.length; i++) {
      var bit = bits.charCodeAt(i) === 49 ? 1 : 0; // '1' === 49
      var msb = (crc >>> 15) & 1;
      crc = (crc << 1) & 0xFFFF;
      if (msb ^ bit) crc ^= CRC16_POLY;
    }
    return crc;
  }
  // Self-check independent of the payload path (same test vector used in
  // tools/test_cm_roundtrip.py): CRC-16/CCITT-FALSE("123456789") == 0x29B1.
  function crcSelfCheck() {
    var bytes = [0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39];
    var bits = "";
    for (var i = 0; i < bytes.length; i++) bits += intToBits(bytes[i], 8);
    return crc16CcittFalseBits(bits) === 0x29b1;
  }

  // =========================================================================
  // §9 — Interleaving. 1:1 из cm_encoder._interleave_physical_to_logical:
  // rows=8, cols=16; mapping[p] = row*cols+col, читая по столбцам (col
  // внешний цикл, row внутренний).
  // =========================================================================
  function interleavePhysicalToLogical() {
    var rows = PROFILE.interleave_rows, cols = PROFILE.interleave_cols;
    var mapping = new Array(rows * cols);
    var p = 0;
    for (var col = 0; col < cols; col++) {
      for (var row = 0; row < rows; row++) {
        mapping[p] = row * cols + col;
        p++;
      }
    }
    return mapping;
  }
  var PHYS_TO_LOG = interleavePhysicalToLogical();

  // decode_sectors: physical (ring,sector) grid -> logical payload bit
  // string + межкольцевая parity-проверка. 1:1 из cm_encoder.decode_sectors.
  function decodeSectorsToBits(data, parity) {
    var n = PROFILE.symbol_count; // 128
    var spr = PROFILE.sectors_per_ring; // 32
    var logicalSymbols = new Array(n);
    for (var p = 0; p < n; p++) {
      var ring = Math.floor(p / spr);
      var sector = p % spr;
      var s = PHYS_TO_LOG[p];
      logicalSymbols[s] = data[ring][sector];
    }
    var bits = "";
    for (var s2 = 0; s2 < n; s2++) bits += intToBits(logicalSymbols[s2], 2);

    var parityMismatches = [];
    if (parity) {
      for (var sec = 0; sec < spr; sec++) {
        var x = 0;
        for (var r = 0; r < PROFILE.data_rings; r++) x ^= data[r][sec];
        if (x !== parity[sec]) parityMismatches.push(sec);
      }
    }
    return { bits: bits, parityMismatches: parityMismatches };
  }

  // decode_payload — 1:1 из cm_encoder.decode_payload. Canonical field order
  // (§19): Magic(8).Version(4).Profile(4).Namespace(8).EntityID(128).
  // ResolverHint(8).TypeHint(4).Reserved(76).CRC16(16).
  function decodePayloadBits(bits) {
    if (bits.length !== PROFILE.capacity_bits) {
      throw new Error("длина bits (" + bits.length + ") != ёмкости профиля (" + PROFILE.capacity_bits + ")");
    }
    var pos = 0;
    function take(width) {
      var chunk = bits.substr(pos, width);
      pos += width;
      return chunk;
    }
    var magicB = take(PROFILE.magic_bits);
    var versionB = take(PROFILE.version_bits);
    var profileB = take(PROFILE.profile_bits);
    var namespaceB = take(PROFILE.namespace_bits);
    var entityB = take(PROFILE.entity_id_bits);
    var resolverB = take(PROFILE.resolver_hint_bits);
    var typeB = take(PROFILE.type_hint_bits);
    var reservedB = take(PROFILE.reserved_bits);
    var crcB = take(PROFILE.crc_bits);

    var fieldsWoCrc = bits.substring(0, PROFILE.capacity_bits - PROFILE.crc_bits);
    var expectedCrc = crc16CcittFalseBits(fieldsWoCrc);
    var actualCrc = bitsToInt(crcB);

    var magic = bitsToInt(magicB);
    var namespaceCode = bitsToInt(namespaceB);
    var resolverCode = bitsToInt(resolverB);
    var typeCode = bitsToInt(typeB);

    // Entity ID — 128 бит, требуется BigInt (обычный Number теряет точность).
    var entityBig = BigInt("0b" + entityB);
    var entityHex = entityBig.toString(16);
    while (entityHex.length < 32) entityHex = "0" + entityHex;
    var entityId = [
      entityHex.substr(0, 8), entityHex.substr(8, 4), entityHex.substr(12, 4),
      entityHex.substr(16, 4), entityHex.substr(20, 12),
    ].join("-");

    var magicValid = magic === PROFILE.magic;
    return {
      magic: magic,
      magic_valid: magicValid,
      version: bitsToInt(versionB),
      profile_id: bitsToInt(profileB),
      namespace: NAMESPACE_TABLE_REV.hasOwnProperty(namespaceCode) ? NAMESPACE_TABLE_REV[namespaceCode] : ("reserved:" + namespaceCode),
      namespace_code: namespaceCode,
      entity_id: entityId,
      resolver_hint: RESOLVER_HINT_TABLE_REV.hasOwnProperty(resolverCode) ? RESOLVER_HINT_TABLE_REV[resolverCode] : ("reserved:" + resolverCode),
      resolver_hint_code: resolverCode,
      type_hint: TYPE_HINT_TABLE_REV.hasOwnProperty(typeCode) ? TYPE_HINT_TABLE_REV[typeCode] : ("reserved:" + typeCode),
      type_hint_code: typeCode,
      crc: actualCrc,
      crc_expected: expectedCrc,
      integrity_ok: actualCrc === expectedCrc && magicValid,
    };
  }

  function decodeSectors(sectors) {
    var r = decodeSectorsToBits(sectors.data, sectors.parity);
    var payload = decodePayloadBits(r.bits);
    payload.bits = r.bits;
    payload.parity_mismatches = r.parityMismatches;
    return payload;
  }

  // =========================================================================
  // Геометрия углов — 1:1 из cm_render.polar_to_xy: 0°="12 часов", по часовой
  // стрелке. x = cx + r*sin(a), y = cy - r*cos(a).
  // =========================================================================
  function polarXY(cx, cy, r, angleDeg) {
    var a = (angleDeg * Math.PI) / 180;
    return [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  }

  // =========================================================================
  // Сэмплер изображения — билинейная выборка яркости (luminance) из
  // ImageData. Работает по КОНТРАСТУ, не по цвету (см. требование §8:
  // "Decoder MUST определять состояние по геометрии и контрасту, а не по
  // цвету") — ниже используются только относительные (адаптивные)
  // яркостные пороги, конкретный оттенок ink не проверяется.
  // =========================================================================
  function makeSampler(imageData) {
    var data = imageData.data, w = imageData.width, h = imageData.height;
    function lumAt(x, y) {
      var idx = (y * w + x) * 4;
      return 0.299 * data[idx] + 0.587 * data[idx + 1] + 0.114 * data[idx + 2];
    }
    return {
      width: w,
      height: h,
      sample: function (x, y) {
        if (x < 0 || y < 0 || x >= w - 1 || y >= h - 1) return 255;
        var x0 = x | 0, y0 = y | 0;
        var fx = x - x0, fy = y - y0;
        var v00 = lumAt(x0, y0), v10 = lumAt(x0 + 1, y0);
        var v01 = lumAt(x0, y0 + 1), v11 = lumAt(x0 + 1, y0 + 1);
        return (
          v00 * (1 - fx) * (1 - fy) + v10 * fx * (1 - fy) +
          v01 * (1 - fx) * fy + v11 * fx * fy
        );
      },
    };
  }

  function circleMean(sampler, cx, cy, r, nSamples) {
    if (r <= 0) return sampler.sample(cx, cy);
    var total = 0;
    for (var i = 0; i < nSamples; i++) {
      var a = (2 * Math.PI * i) / nSamples;
      total += sampler.sample(cx + r * Math.cos(a), cy + r * Math.sin(a));
    }
    return total / nSamples;
  }

  // Радиальный профиль CM Core (§10): цветное кольцо-вырез r=130..176
  // (dark) / белый зазор r=176..190 перед Sync-кольцом (light). НАРОЧНО
  // не используем r<130 (точка/логотип) — при --center-logo туда
  // вписывается произвольная картинка (r<=127), поэтому декодер обязан
  // одинаково находить центр по кольцу ядра для ОБОИХ вариантов метки
  // (см. коммит 12e119e "рамка по краю марки и опциональный логотип").
  var CORE_PROBES = [
    [(GEO.CORE_WHITE_R + GEO.CORE_OUT_R) / 2, "dark"],
    [(GEO.CORE_OUT_R + GEO.SYNC_IN_R) / 2, "light"],
  ];

  // Проверка "сплошной заливки" именно КОЛЬЦА (не центра — см. выше): 12
  // точек на окружности r≈153*s (середина цветного кольца ядра). ВСЕ они
  // должны быть тёмными и однородными — так отличается настоящее сплошное
  // кольцо (полная окружность) от случайного совпадения яркости на
  // границе секторов данных/sync (там тёмная область не покрывает полный
  // круг вокруг случайной точки).
  function ringUniformity(sampler, cx, cy, r, n) {
    var maxV = -Infinity, sum = 0;
    for (var i = 0; i < n; i++) {
      var a = (2 * Math.PI * i) / n;
      var v = sampler.sample(cx + r * Math.cos(a), cy + r * Math.sin(a));
      if (v > maxV) maxV = v;
      sum += v;
    }
    return { maxV: maxV, avgV: sum / n };
  }

  function coreScore(sampler, cx, cy, s, nSamples) {
    var darkSum = 0, darkN = 0, lightSum = 0, lightN = 0;
    for (var i = 0; i < CORE_PROBES.length; i++) {
      var r = CORE_PROBES[i][0] * s;
      var kind = CORE_PROBES[i][1];
      var v = circleMean(sampler, cx, cy, r, nSamples);
      if (kind === "dark") { darkSum += v; darkN++; } else { lightSum += v; lightN++; }
    }
    var darkAvg = darkSum / darkN, lightAvg = lightSum / lightN;
    var contrast = lightAvg - darkAvg;
    var score = (255 - darkAvg) * darkN + lightAvg * lightN;
    if (contrast < 20) score -= 100000; // недостаточно контраста — не ядро
    var ring = ringUniformity(sampler, cx, cy, ((GEO.CORE_WHITE_R + GEO.CORE_OUT_R) / 2) * s, 12);
    score -= ring.maxV * 4; // штраф пропорционален не-однородности кольца ядра
    return score;
  }

  function linspace(a, b, n) {
    if (n <= 1) return [a];
    var out = new Array(n), step = (b - a) / (n - 1);
    for (var i = 0; i < n; i++) out[i] = a + step * i;
    return out;
  }

  /**
   * Ищет CM Core (bullseye) вокруг (guessCx,guessCy) с масштабом около
   * guessS (пикселей на design-unit). Возвращает {cx,cy,s,score}.
   * Грубый перебор по сетке + локальное уточнение координатным спуском —
   * дёшево (десятки тысяч выборок), проверено в оффлайн-прототипе
   * (scratchpad/proto_detect.py) на смещённой/повёрнутой/масштабированной/
   * размытой синтетической метке — распознаётся уверенно.
   */
  function findCore(sampler, guessCx, guessCy, guessS, opts) {
    opts = opts || {};
    var centerSearchPx = opts.centerSearchPx != null ? opts.centerSearchPx : guessS * 120;
    var scaleRange = opts.scaleRange || [0.6, 1.6];
    var nSamples = opts.nSamples || 16;
    var gridN = opts.gridN || 5;

    var best = null;
    var scales = linspace(guessS * scaleRange[0], guessS * scaleRange[1], gridN);
    var dxs = linspace(-centerSearchPx, centerSearchPx, gridN);
    var dys = linspace(-centerSearchPx, centerSearchPx, gridN);
    for (var si = 0; si < scales.length; si++) {
      var s = scales[si];
      for (var xi = 0; xi < dxs.length; xi++) {
        for (var yi = 0; yi < dys.length; yi++) {
          var cx = guessCx + dxs[xi], cy = guessCy + dys[yi];
          var sc = coreScore(sampler, cx, cy, s, nSamples);
          if (!best || sc > best.score) best = { cx: cx, cy: cy, s: s, score: sc };
        }
      }
    }
    // Локальное уточнение — сеточный pattern-search (26 соседей: все
    // комбинации {-1,0,1} по cx, cy и множителю s одновременно), с
    // уменьшающимся шагом на каждой итерации. Обычный покоординатный
    // спуск (менять только одну ось за раз) здесь застревал в ложном
    // локальном максимуме, т.к. верная точка требует ОДНОВРЕМЕННОГО сдвига
    // по нескольким осям сразу — 26-соседняя решётка это покрывает.
    var cx = best.cx, cy = best.cy, s = best.s, sc = best.score;
    var radiusPx = Math.max(6, centerSearchPx / Math.max(gridN - 1, 1));
    var scaleFrac = (scaleRange[1] - scaleRange[0]) / Math.max(gridN - 1, 1) / 2;
    var deltas = [-1, 0, 1];
    for (var iter = 0; iter < 7; iter++) {
      var bestLocal = { cx: cx, cy: cy, s: s, score: sc };
      for (var di = 0; di < deltas.length; di++) {
        for (var dj = 0; dj < deltas.length; dj++) {
          for (var dk = 0; dk < deltas.length; dk++) {
            if (deltas[di] === 0 && deltas[dj] === 0 && deltas[dk] === 0) continue;
            var ncx = cx + deltas[di] * radiusPx;
            var ncy = cy + deltas[dj] * radiusPx;
            var ns2 = s * (1 + deltas[dk] * scaleFrac);
            var nsc = coreScore(sampler, ncx, ncy, ns2, nSamples);
            if (nsc > bestLocal.score) bestLocal = { cx: ncx, cy: ncy, s: ns2, score: nsc };
          }
        }
      }
      cx = bestLocal.cx; cy = bestLocal.cy; s = bestLocal.s; sc = bestLocal.score;
      radiusPx *= 0.5;
      scaleFrac *= 0.5;
    }
    // Грубого совпадения по круговым пробам ядра (см. выше) достаточно,
    // чтобы найти центр, но НЕ достаточно, чтобы точно определить масштаб:
    // между Core (r<=176) и Sync-кольцом (r=190) всего 14 design-unit
    // зазора — окно допустимой ошибки масштаба по одной этой пробе
    // получается непропорционально широким (~±3%), а такая ошибка на
    // кольце Parity (r=420) — это уже ±11px абсолютной ошибки при
    // толщине кольца/зазора всего 30/6 design units. Поэтому масштаб
    // уточняется в два шага:
    //  1) грубо — бисекцией границы кольца ядра (130/176, не зависит от
    //     наличия логотипа в центре);
    //  2) точно — бисекцией внешней рамки (471/477, коммит 12e119e): она
    //     физически ближе к кольцам данных/чётности (r<=420), чем ядро,
    //     поэтому даёт кратно меньшую экстраполяционную ошибку именно там,
    //     где она важнее всего для чтения бит.
    var refined = refineScaleByCoreEdges(sampler, cx, cy, s);
    if (refined) { s = refined.s; }
    var refinedBorder = refineScaleByBorderEdges(sampler, cx, cy, s);
    if (refinedBorder) { s = refinedBorder.s; }

    // Финальный проход только по позиции (масштаб уже точный) — убирает
    // остаточное смещение центра, накопленное, пока s ещё не был уточнён.
    var posRadius = Math.max(radiusPx, 4);
    for (var pit = 0; pit < 5; pit++) {
      var bestPos = { cx: cx, cy: cy, score: coreScore(sampler, cx, cy, s, nSamples) };
      for (var pdi = 0; pdi < deltas.length; pdi++) {
        for (var pdj = 0; pdj < deltas.length; pdj++) {
          if (deltas[pdi] === 0 && deltas[pdj] === 0) continue;
          var pcx = cx + deltas[pdi] * posRadius, pcy = cy + deltas[pdj] * posRadius;
          var psc = coreScore(sampler, pcx, pcy, s, nSamples);
          if (psc > bestPos.score) bestPos = { cx: pcx, cy: pcy, score: psc };
        }
      }
      cx = bestPos.cx; cy = bestPos.cy; sc = bestPos.score;
      posRadius *= 0.5;
    }

    return { cx: cx, cy: cy, s: s, score: sc };
  }

  function bisectEdge(sampler, cx, cy, rLo, rHi, wantDarkAtLo, threshold, iters) {
    for (var i = 0; i < iters; i++) {
      var rMid = (rLo + rHi) / 2;
      var v = circleMean(sampler, cx, cy, rMid, 24);
      var isDark = v < threshold;
      if (isDark === wantDarkAtLo) rLo = rMid; else rHi = rMid;
    }
    return (rLo + rHi) / 2;
  }

  /**
   * Уточняет масштаб (design-unit -> px) точной бисекцией двух известных
   * границ кольца CM Core (130 — граница белого выреза/кольца, 176 —
   * внешняя граница кольца), не зависящих от Entity ID и одинаковых для
   * обоих вариантов метки (с логотипом и без). Возвращает {s} или null,
   * если границы не нашлись (низкий контраст / это не ядро).
   */
  function refineScaleByCoreEdges(sampler, cx, cy, s0) {
    var darkRef = circleMean(sampler, cx, cy, ((GEO.CORE_WHITE_R + GEO.CORE_OUT_R) / 2) * s0, 24);
    var lightRef = circleMean(sampler, cx, cy, ((GEO.CORE_OUT_R + GEO.SYNC_IN_R) / 2) * s0, 24);
    if (lightRef - darkRef < 20) return null;
    var threshold = (darkRef + lightRef) / 2;

    var r1 = bisectEdge(sampler, cx, cy, 100 * s0, 160 * s0, false, threshold, 16); // light(гарантированно только r<130 без логотипа...)->dark(кольцо), ~130
    var r2 = bisectEdge(sampler, cx, cy, 150 * s0, 205 * s0, true, threshold, 16); // dark(кольцо)->light(зазор), ~176

    var s1 = r1 / GEO.CORE_WHITE_R;
    var s2 = r2 / GEO.CORE_OUT_R;
    var sEst = (s1 + s2) / 2;
    var spread = Math.abs(s1 - s2);
    if (spread > sEst * 0.06) return null;
    return { s: sEst, spread: spread };
  }

  /**
   * Точное уточнение масштаба по внешней рамке метки (коммит 12e119e,
   * cm_render.py BORDER_IN_R=471 / BORDER_OUT_R=477, замкнутая окружность
   * толщиной 6 design-units) — бисекция обеих границ обводки. Рамка
   * физически ближе к кольцам данных (r<=420), чем Core (r<=190), поэтому
   * даёт заметно более точную калибровку масштаба именно там, где нужно
   * читать биты. Возвращает {s} или null, если рамка не нашлась (кадр
   * обрезан, низкий контраст, и т.п. — тогда используется грубая оценка
   * по кольцу ядра из refineScaleByCoreEdges).
   */
  function refineScaleByBorderEdges(sampler, cx, cy, s0) {
    var lightRef = circleMean(sampler, cx, cy, ((GEO.PARITY_OUT_R + GEO.BORDER_IN_R) / 2) * s0, 24);
    var darkRef = circleMean(sampler, cx, cy, GEO.BORDER_MID_R * s0, 24);
    if (lightRef - darkRef < 20) return null;
    var threshold = (darkRef + lightRef) / 2;

    var r1 = bisectEdge(sampler, cx, cy, 430 * s0, GEO.BORDER_MID_R * s0, false, threshold, 18); // light(зазор Parity->рамка)->dark(рамка), ~471
    var r2 = bisectEdge(sampler, cx, cy, GEO.BORDER_MID_R * s0, 498 * s0, true, threshold, 18); // dark(рамка)->light(quiet zone), ~477

    var s1 = r1 / GEO.BORDER_IN_R;
    var s2 = r2 / GEO.BORDER_OUT_R;
    var sEst = (s1 + s2) / 2;
    var spread = Math.abs(s1 - s2);
    if (spread > sEst * 0.04) return null; // рамка тоньше (6 units) — жёстче порог согласованности
    return { s: sEst, spread: spread };
  }

  // Теоретический угловой профиль Sync/Orientation-кольца (§11) при
  // повороте 0 — используется как "шаблон" для кросс-корреляции.
  function buildTheoreticalSyncProfile() {
    var nAng = 360;
    var inner = new Array(nAng).fill(0);
    var outer = new Array(nAng).fill(0);
    for (var i = 0; i < ORIENTATION_MARKERS.length; i++) {
      var m = ORIENTATION_MARKERS[i];
      var a0 = m.base_angle_deg, a1 = a0 + m.width_deg;
      for (var a = Math.floor(a0); a < Math.floor(a1); a++) {
        var aa = ((a % nAng) + nAng) % nAng;
        if (m.shape === "SOLID") { inner[aa] = 1; outer[aa] = 1; }
        else if (m.shape === "OUTER_HALF") { outer[aa] = 1; }
        else if (m.shape === "INNER_HALF") { inner[aa] = 1; }
        else if (m.shape === "SPLIT") { inner[aa] = 1; outer[aa] = 1; }
      }
    }
    return { inner: inner, outer: outer };
  }
  var SYNC_TEMPLATE = buildTheoreticalSyncProfile();

  /**
   * Определяет поворот метки (0..359°) кросс-корреляцией наблюдаемого
   * углового профиля Sync-кольца с теоретическим шаблоном
   * ORIENTATION_MARKERS. Возвращает {delta, score}.
   */
  function findRotation(sampler, cx, cy, s) {
    var nAng = 360;
    var rInnerMid = (GEO.SYNC_IN_R + (GEO.SYNC_IN_R + GEO.SYNC_OUT_R) / 2) / 2;
    var rOuterMid = ((GEO.SYNC_IN_R + GEO.SYNC_OUT_R) / 2 + GEO.SYNC_OUT_R) / 2;
    var obsInner = new Array(nAng), obsOuter = new Array(nAng);
    var sumI = 0, sumO = 0;
    for (var a = 0; a < nAng; a++) {
      var pi = polarXY(cx, cy, rInnerMid * s, a);
      var po = polarXY(cx, cy, rOuterMid * s, a);
      var vi = 255 - sampler.sample(pi[0], pi[1]);
      var vo = 255 - sampler.sample(po[0], po[1]);
      obsInner[a] = vi; obsOuter[a] = vo;
      sumI += vi; sumO += vo;
    }
    var mi = sumI / nAng, mo = sumO / nAng;
    for (var a2 = 0; a2 < nAng; a2++) { obsInner[a2] -= mi; obsOuter[a2] -= mo; }

    var thInner = SYNC_TEMPLATE.inner.slice(), thOuter = SYNC_TEMPLATE.outer.slice();
    var tmi = thInner.reduce(function (x, y) { return x + y; }, 0) / nAng;
    var tmo = thOuter.reduce(function (x, y) { return x + y; }, 0) / nAng;
    for (var a3 = 0; a3 < nAng; a3++) { thInner[a3] -= tmi; thOuter[a3] -= tmo; }

    var bestDelta = 0, bestScore = -Infinity;
    for (var delta = 0; delta < nAng; delta++) {
      var score = 0;
      for (var a4 = 0; a4 < nAng; a4++) {
        var idx = ((a4 - delta) % nAng + nAng) % nAng;
        score += obsInner[a4] * thInner[idx] + obsOuter[a4] * thOuter[idx];
      }
      if (score > bestScore) { bestScore = score; bestDelta = delta; }
    }
    return { delta: bestDelta, score: bestScore };
  }

  /**
   * Сэмплирует все 4 кольца данных + parity-кольцо в (inner,outer)
   * яркостные пары на сектор, с учётом найденного поворота delta.
   */
  function sampleRings(sampler, cx, cy, s, delta) {
    var spr = PROFILE.sectors_per_ring;
    var step = 360 / spr;
    var gap = step * GEO.SECTOR_GAP_FRACTION;
    var rings = GEO.DATA_RING_RADII.concat([[GEO.PARITY_IN_R, GEO.PARITY_OUT_R]]);

    function bandMean(rIn, rOut, aStart, aEnd) {
      var total = 0, n = 0;
      for (var ai = 0; ai < 3; ai++) {
        var a = aStart + ((aEnd - aStart) * (ai + 0.5)) / 3 + delta;
        for (var ri = 0; ri < 2; ri++) {
          var r = rIn + ((rOut - rIn) * (ri + 0.5)) / 2;
          var p = polarXY(cx, cy, r * s, a);
          total += sampler.sample(p[0], p[1]);
          n++;
        }
      }
      return total / n;
    }

    var raw = []; // raw[ringIdx][sector] = {inner, outer}
    for (var ringI = 0; ringI < rings.length; ringI++) {
      var rIn = rings[ringI][0], rOut = rings[ringI][1], rMid = (rIn + rOut) / 2;
      var row = new Array(spr);
      for (var sec = 0; sec < spr; sec++) {
        var aStart = sec * step + gap / 2, aEnd = (sec + 1) * step - gap / 2;
        row[sec] = {
          inner: bandMean(rIn, rMid, aStart, aEnd),
          outer: bandMean(rMid, rOut, aStart, aEnd),
        };
      }
      raw.push(row);
    }
    return raw; // raw[0..3] = data rings, raw[4] = parity
  }

  /**
   * Классифицирует sector states по КОНТРАСТУ: адаптивный порог на кольцо
   * = середина между min и max наблюдённых яркостей этого кольца (а не
   * фиксированный цвет/яркость) — так требует §8 спецификации.
   */
  function classifyRings(raw) {
    var spr = PROFILE.sectors_per_ring;
    var data = [[], [], [], []];
    var parity = [];
    for (var ringI = 0; ringI < raw.length; ringI++) {
      var vals = [];
      for (var sec = 0; sec < spr; sec++) {
        vals.push(raw[ringI][sec].inner, raw[ringI][sec].outer);
      }
      var lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
      var thresh = (lo + hi) / 2;
      var contrastOk = (hi - lo) > 15;
      var out = new Array(spr);
      for (var sec2 = 0; sec2 < spr; sec2++) {
        var innerDark = raw[ringI][sec2].inner < thresh;
        var outerDark = raw[ringI][sec2].outer < thresh;
        var state;
        if (innerDark && outerDark) state = STATE_FULL;
        else if (innerDark) state = STATE_INNER;
        else if (outerDark) state = STATE_OUTER;
        else state = STATE_EMPTY;
        out[sec2] = state;
      }
      if (ringI < 4) data[ringI] = out; else parity = out;
      if (!contrastOk && ringI === 0) { /* см. lowContrast в attemptDecode */ }
    }
    return { data: data, parity: parity, ringContrast: raw.map(function (row) {
      var vals = [];
      for (var i = 0; i < row.length; i++) vals.push(row[i].inner, row[i].outer);
      return Math.max.apply(null, vals) - Math.min.apply(null, vals);
    }) };
  }

  /**
   * Полный конвейер: sampler + предположение о (cx,cy,s) -> результат
   * распознавания. Не бросает исключения на "плохом" кадре — возвращает
   * {ok:false, reason} вместо мусора.
   */
  function attemptDecode(sampler, guessCx, guessCy, guessS, opts) {
    opts = opts || {};
    var core = findCore(sampler, guessCx, guessCy, guessS, opts.core);
    if (core.score < 0) {
      return { ok: false, reason: "core_not_found", core: core };
    }
    var rot = findRotation(sampler, core.cx, core.cy, core.s);
    var raw = sampleRings(sampler, core.cx, core.cy, core.s, rot.delta);
    var classified = classifyRings(raw);
    var minContrast = Math.min.apply(null, classified.ringContrast);
    if (minContrast < 15) {
      return { ok: false, reason: "low_contrast", core: core, rotation: rot, ringContrast: classified.ringContrast };
    }
    var payload;
    try {
      payload = decodeSectors({ data: classified.data, parity: classified.parity });
    } catch (e) {
      return { ok: false, reason: "decode_error", error: String(e), core: core, rotation: rot };
    }
    return {
      ok: true,
      core: core,
      rotation: rot,
      payload: payload,
      ringContrast: classified.ringContrast,
    };
  }

  // =========================================================================
  // Экспорт
  // =========================================================================
  var CMScanCore = {
    PROFILE: PROFILE,
    GEO: GEO,
    ORIENTATION_MARKERS: ORIENTATION_MARKERS,
    STATE_EMPTY: STATE_EMPTY, STATE_INNER: STATE_INNER, STATE_OUTER: STATE_OUTER, STATE_FULL: STATE_FULL,
    crc16CcittFalseBits: crc16CcittFalseBits,
    crcSelfCheck: crcSelfCheck,
    interleavePhysicalToLogical: interleavePhysicalToLogical,
    decodeSectorsToBits: decodeSectorsToBits,
    decodePayloadBits: decodePayloadBits,
    decodeSectors: decodeSectors,
    polarXY: polarXY,
    makeSampler: makeSampler,
    findCore: findCore,
    findRotation: findRotation,
    sampleRings: sampleRings,
    classifyRings: classifyRings,
    attemptDecode: attemptDecode,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = CMScanCore;
  } else {
    global.CMScanCore = CMScanCore;
  }
})(typeof window !== "undefined" ? window : globalThis);

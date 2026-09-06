#!/usr/bin/env python3
"""Генератор человекочитаемой страницы записи реестра.

Запись в реестре — JSON: это формат для машин. Человеку, отсканировавшему
марку, нужна страница, а не массив полей. Скрипт делает из записи статический
HTML: без скриптов, без внешних запросов, открывается где угодно, включая
телефон в цеху с плохой связью.

    python3 tools/render_record.py registry/<entity-id>.json --out <файл.html>
"""
import argparse
import html
import json
import pathlib

FRESHNESS_JS = """
<script>
/* Единственный скрипт на странице: сравнить срок годности с датой устройства.
   Без него пришлось бы либо промолчать о свежести, либо утверждать её, не зная
   сегодняшнего числа. */
(function () {
  var el = document.getElementById("freshness");
  if (!el) return;
  var best = new Date(el.getAttribute("data-best"));
  if (isNaN(best)) return;
  var expired = best < new Date();
  el.className = "fresh " + (expired ? "bad" : "ok");
  el.textContent = (expired ? "Срок истёк " : "Годен до ") + best.toLocaleDateString();
})();
</script>
"""

CSS = """
:root { --ink:#04101F; --accent:#4800FF; --muted:#5a6472; --line:#e3e6ec; --bg:#ffffff; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#eef1f6; --accent:#8f6bff; --muted:#9aa4b2; --line:#232833; --bg:#0d1117; }
}
* { box-sizing:border-box; }
body { margin:0; padding:24px 16px 64px; background:var(--bg); color:var(--ink);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
.wrap { max-width:720px; margin:0 auto; }
.badge { display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px;
  letter-spacing:.06em; text-transform:uppercase; background:var(--accent); color:#fff; }
h1 { font-size:26px; line-height:1.25; margin:16px 0 4px; }
.sub { color:var(--muted); margin:0 0 28px; }
.card { border:1px solid var(--line); border-radius:14px; padding:18px; margin:0 0 16px; }
.card h2 { font-size:13px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--muted); margin:0 0 12px; font-weight:600; }
dl { display:grid; grid-template-columns:minmax(120px,auto) 1fr; gap:8px 16px; margin:0; }
dt { color:var(--muted); }
dd { margin:0; overflow-wrap:anywhere; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px;
  overflow-wrap:anywhere; word-break:break-all; }
.doc { padding:12px 0; border-top:1px solid var(--line); }
.doc:first-of-type { border-top:0; padding-top:0; }
.doc b { font-weight:600; display:block; }
.hash { color:var(--muted); font-size:12px; display:block; margin-top:4px;
  overflow-wrap:anywhere; word-break:break-all; }
.note { color:var(--muted); font-size:14px; }
a { color:var(--accent); }
footer { margin-top:32px; color:var(--muted); font-size:13px; }
/* Телефон — основной сценарий: человек сканирует метку в магазине или цеху.
   Двухколоночные списки на узком экране разъезжаются, а хэши и идентификаторы
   вылезают за край, поэтому на узких экранах колонки складываются в одну. */
@media (max-width: 560px) {
  body { padding:16px 12px 48px; }
  .wrap { max-width:100%; }
  h1 { font-size:22px; }
  .card { padding:14px; border-radius:12px; }
  dl, .rows { grid-template-columns:1fr; gap:2px 0; }
  dl dt, .rows div:nth-child(odd) { font-size:13px; margin-top:10px; }
  dl dd, .rows div:nth-child(even) { margin-bottom:2px; }
  .big { font-size:18px; }
  code { font-size:12px; }
}
.hero { font-size:15px; }
.big { font-size:20px; font-weight:600; margin:2px 0 0; }
.rows { display:grid; grid-template-columns:minmax(140px,auto) 1fr; gap:8px 16px; }
.rows div:nth-child(odd) { color:var(--muted); }
ul.comp { margin:0; padding-left:20px; }
.fresh { display:inline-block; padding:6px 12px; border-radius:10px; font-weight:600; }
.fresh.ok { background:#e7f7ec; color:#0d6b2f; }
.fresh.bad { background:#fdeaea; color:#8f1d1d; }
@media (prefers-color-scheme: dark) {
  .fresh.ok { background:#123021; color:#7fe0a3; }
  .fresh.bad { background:#3a1717; color:#ff9a9a; }
}
.warn { border-left:3px solid var(--accent); padding-left:12px; }
.top { display:flex; align-items:center; gap:12px; margin:0 0 20px; }
.top img { width:44px; height:44px; }
.top .name { font-weight:700; letter-spacing:.04em; font-size:18px; }
.top .kind { color:var(--muted); font-size:13px; }
.owner { display:flex; align-items:center; gap:10px; margin-top:20px;
  padding-top:18px; border-top:1px solid var(--line); }
.owner img { width:28px; height:28px; }
.owner span { color:var(--muted); font-size:13px; }
"""


def _passport_html(pp: dict, e) -> str:
    """Паспорт партии — то, ради чего покупатель вообще сканирует марку."""
    if not pp:
        return ""
    prod = pp.get("producer", {}) or {}
    batch = pp.get("batch", {}) or {}
    attrs = pp.get("attributes", {}) or {}
    comp = pp.get("composition", []) or []
    nutr = pp.get("nutrition", {}) or {}
    certs = pp.get("certificates", []) or []

    rows = "".join(f"<div>{e(k)}</div><div>{e(str(v))}</div>" for k, v in attrs.items())
    nutr_rows = "".join(f"<div>{e(k)}</div><div>{e(str(v))}</div>" for k, v in nutr.items())
    comp_html = "".join(f"<li>{e(c)}</li>" for c in comp)
    cert_html = "".join(
        f'<div class="doc"><b>{e(c.get("name",""))}</b><span class="hash">'
        f'{e(c.get("number",""))}{" · " + e(c.get("commitment","")) if c.get("commitment") else ""}'
        f"</span></div>" for c in certs)

    best = batch.get("best_before", "")
    fresh = (f'<p><span class="fresh" id="freshness" data-best="{e(best)}">Годен до '
             f'{e(best[:10])}</span></p>') if best else ""

    return f"""
<div class="card hero">
  <h2>Продукт</h2>
  <p class="big">{e(pp.get('product_name',''))}</p>
  <p class="sub" style="margin:6px 0 0">{e(prod.get('name',''))}{
      ', ' + e(prod.get('country','')) if prod.get('country') else ''}</p>
  {'<p>' + e(pp.get('description','')) + '</p>' if pp.get('description') else ''}
  {fresh}
</div>

<div class="card"><h2>Партия</h2><div class="rows">
  <div>Номер партии</div><div>{e(batch.get('code',''))}</div>
  <div>Выпущена</div><div>{e(batch.get('produced_at','')[:16].replace('T',' '))}</div>
  {'<div>Годен до</div><div>' + e(batch.get('best_before','')[:16].replace('T',' ')) + '</div>' if best else ''}
  {'<div>Хранение</div><div>' + e(batch.get('storage','')) + '</div>' if batch.get('storage') else ''}
  {'<div>Площадка</div><div>' + e(prod.get('site','')) + '</div>' if prod.get('site') else ''}
  {'<div>Идентификатор</div><div><code>' + e(prod.get('identifier','')) + '</code></div>' if prod.get('identifier') else ''}
</div></div>

{'<div class="card"><h2>Характеристики</h2><div class="rows">' + rows + '</div></div>' if rows else ''}
{'<div class="card"><h2>Состав</h2><ul class="comp">' + comp_html + '</ul></div>' if comp_html else ''}
{'<div class="card"><h2>Пищевая ценность на 100 мл</h2><div class="rows">' + nutr_rows + '</div></div>' if nutr_rows else ''}
{'<div class="card"><h2>Документы</h2>' + cert_html + '</div>' if cert_html else ''}
"""


def render(record: dict, json_url: str) -> str:
    p = record.get("payload", {})
    e = html.escape
    passport = _passport_html(record.get("product_passport"), e)
    freshness_script = FRESHNESS_JS if record.get("product_passport") else ""
    docs = ""
    for d in p.get("documents", []):
        docs += (f'<div class="doc"><b>{e(d.get("name",""))}</b>'
                 f'<span class="hash"><code>{e(d.get("commitment",""))}</code></span></div>\n')
    naming = "".join(
        f"<dt>{e(k)}</dt><dd>{e(v)}</dd>" for k, v in (p.get("naming") or {}).items()
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(p.get('title','Record'))} — CYRQODE</title>
<style>{CSS}</style></head><body><div class="wrap">
<div class="top">
  <img src="/cyrqode/assets/cyrqode-symbol.png" alt="CYRQODE">
  <div><div class="name">CYRQODE</div><div class="kind">запись реестра</div></div>
</div>
<h1>{e(p.get('title',''))}</h1>
<p class="sub">{e(p.get('date',''))} · {e(p.get('author',''))}{
    ' · ' + e(p.get('author_role','')) if p.get('author_role') else ''}</p>

{passport}
{'<div class="card"><h2>What happened</h2><p>' + e(p.get('summary','')) + '</p></div>' if p.get('summary') else ''}

{'<div class="card"><h2>Naming</h2><dl>' + naming + '</dl></div>' if naming else ''}

<div class="card"><h2>Identity</h2><dl>
<dt>Entity</dt><dd><code>{e(record.get('subject_entity_id',''))}</code></dd>
<dt>Namespace</dt><dd>{e(record.get('namespace_id',''))}</dd>
<dt>Event</dt><dd><code>{e(record.get('event_id',''))}</code></dd>
<dt>Type</dt><dd>{e(record.get('event_type',''))}</dd>
<dt>Time</dt><dd>{e(record.get('event_time',''))}</dd>
<dt>Proof level</dt><dd>{e(record.get('proof_level',''))}</dd>
</dl></div>

{'<div class="card"><h2>Documents proved by this record</h2>' + docs +
 '<p class="note">' + e(p.get('documents_note','')) + '</p></div>' if docs else ''}

<div class="card"><h2>Why the mark carries no data</h2>
<p class="note">{e(p.get('note',''))}</p></div>

<footer>
<p><a href="{e(json_url)}">Raw record (JSON)</a> · the machine-readable source of this page.</p>
<div class="owner">
  <img src="/cyrqode/assets/bizdnai-logo.png" alt="BizDNAi">
  <span>Технология BizDNAi</span>
</div>
<p>Proof level {e(record.get('proof_level','PL-0'))}: an owner statement. Signatures and
independent witnessing are a later layer, and this page does not pretend otherwise.</p>
</footer>
</div>{freshness_script}
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("record", help="путь к JSON записи реестра")
    ap.add_argument("--out", required=True, help="путь к выходному .html")
    ap.add_argument("--json-url", default="", help="ссылка на сырой JSON")
    a = ap.parse_args()
    rec = json.loads(pathlib.Path(a.record).read_text(encoding="utf-8"))
    url = a.json_url or f"https://github.com/Kabzhanov/cyrqode/blob/main/registry/{rec['subject_entity_id']}.json"
    out = pathlib.Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rec, url), encoding="utf-8")
    print(f"Записано {out} ({out.stat().st_size} байт)")


if __name__ == "__main__":
    main()

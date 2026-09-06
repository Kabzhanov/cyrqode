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
code { font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:13px; }
.doc { padding:12px 0; border-top:1px solid var(--line); }
.doc:first-of-type { border-top:0; padding-top:0; }
.doc b { font-weight:600; display:block; }
.hash { color:var(--muted); font-size:12px; }
.note { color:var(--muted); font-size:14px; }
a { color:var(--accent); }
footer { margin-top:32px; color:var(--muted); font-size:13px; }
"""


def render(record: dict, json_url: str) -> str:
    p = record.get("payload", {})
    e = html.escape
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
<span class="badge">CYRQODE record</span>
<h1>{e(p.get('title',''))}</h1>
<p class="sub">{e(p.get('date',''))} · {e(p.get('author',''))}{
    ' · ' + e(p.get('author_role','')) if p.get('author_role') else ''}</p>

<div class="card"><h2>What happened</h2><p>{e(p.get('summary',''))}</p></div>

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
<p>Proof level {e(record.get('proof_level','PL-0'))}: an owner statement. Signatures and
independent witnessing are a later layer, and this page does not pretend otherwise.</p>
</footer>
</div></body></html>
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

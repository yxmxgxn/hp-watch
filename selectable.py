"""選択式フィード（/custom.xml?site=…&kind=…&who=…&cat=…&general=1&q=…&x=…）の元データと、選ぶ画面を作る

  data/{site}-{kind}.json … 記事（人・グループ・カテゴリ付き）。Worker（watcher/src/custom.js）が読んで、条件に合うものだけRSSにする
  data/options.json       … 選べるもの（サイト・種類・人／グループ・カテゴリ）。選ぶ画面と Worker が読む
  custom/index.html       … 選ぶ画面

方針: サイトはまたがない。お知らせとスケジュールは別。1本のフィード＝「1つのサイト × 片方の種類」の中で選ぶ。
  who … 人・グループ（どれか）／cat … カテゴリ（どれか）／general … 特定の人・グループに紐づかない全体のものだけ
  general=1 … 全体のものだけ／general=0 … 全体のものを除く（タグが付いた記事だけ）
  q … 含む言葉（全部を含む）／x … 含まない言葉（どれかを含むものを除く）。言葉は題名（name）が対象。本文は持たない・公開しない
"""
import datetime as dt
import json
import os
import re

DATA_LIMIT = 400  # 1つの元データに残す記事数（新しい順）
GROUP_TYPES = {"group", "unit"}  # ほかの種別（member・solo）は「人」


def write(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def iso(v):
    return v.isoformat() if isinstance(v, dt.datetime) else v


def people_map(profiles):
    """プロフィールID → (キー, 名前, 種別)。キーは公式の slug。無い・重複するものは ID から作る"""
    pm, used = {}, set()
    for pid in sorted(profiles, key=lambda k: int(k)):
        p = profiles[pid]
        key = p.get("slug") or f"id{pid}"
        if key in used:
            key = f"{key}-{pid}"
        used.add(key)
        pm[str(pid)] = (key, p.get("nameJa") or key, p.get("type") or "")
    return pm


def who_of(ids, pm):
    """IDのリスト → キーのリスト（プロフィールに無いIDは飛ばす。重複は除く）"""
    out = []
    for i in ids or []:
        k = (pm.get(str(i)) or (None,))[0]
        if k and k not in out:
            out.append(k)
    return out


def is_general(who, tags=()):
    """特定の人・グループに紐づかない全体のもの。誰にも紐づかない、またはハロプロ全体のタグ（HELLO! PROJECT）が付いている"""
    return not who or "HELLO! PROJECT" in (tags or ())


# 記事の題名は、フィードでは「日付 [カテゴリ] 題名」「[カテゴリ] 日付 時刻 題名 [FC限定]」のように飾っている。言葉を探すときは、飾りを除いた題名だけを見る
_DECOR = [
    re.compile(r"^\s*\d{4}\.\d{1,2}\.\d{1,2}\s+\[[^\]]*\]\s*"),                         # 2026.11.19 [イベント] 題名
    re.compile(r"^\s*\[[A-Z]+\]\s*\d{1,2}/\d{1,2}\([月火水木金土日]\)\s*(?:\d{1,2}:\d{2}\s*)?"),  # [RADIO] 11/28(土) 19:00 題名
]
_TAIL = re.compile(r"(?:\s+\[[^\]]*\])+\s*$")  # 末尾の [FC限定] [中止] [掲載済]


def plain_title(title):
    t = title or ""
    for r in _DECOR:
        t = r.sub("", t, count=1)
    return _TAIL.sub("", t).strip() or (title or "")


def make_source(out, site, site_label, kind, kind_label, items, who_info, cat_info, who_all=False, note=None):
    """元データを書いて、選ぶ画面用の選択肢を返す。
    items: "who"（キーのリスト）・"cats"（カテゴリのキーのリスト）・"general"・"name"（飾りの無い題名）が付いた記事
    who_info: [(キー, 名前, 種別)] をプロフィールの順に。cat_info: [(キー, 名前)] を画面に出す順に。
    名前が空のプロフィール（未発表のユニットなど）は出さない。カテゴリは、記事がまだ無くても全部出す。人・グループは、記事に出てくるものだけ
    （who_all なら全員。スケジュールは新しく載ったものから溜めるので、使い始めは記事が少なくても、見たい人を選べるようにする）"""
    rows = sorted(items, key=lambda i: (iso(i["date"]), str(i["sort"])), reverse=True)[:DATA_LIMIT]
    used_who = {k for i in rows for k in i.get("who") or []}
    data = {"items": []}
    for i in rows:
        row = {
            "guid": i["guid"], "title": i["title"], "name": i.get("name") or plain_title(i["title"]), "link": i["link"],
            "date": iso(i["date"]), "html": i["html"], "categories": i["categories"],
            "cats": i.get("cats") or [], "who": i.get("who") or [],
        }
        if i.get("general"):
            row["general"] = True
        data["items"].append(row)
    file = f"/data/{site}-{kind}.json"
    write(os.path.join(out, file.lstrip("/")), json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    src = {
        "site": site, "siteLabel": site_label, "kind": kind, "kindLabel": kind_label, "file": file, "count": len(rows),
        "who": [{"key": k, "label": n, "type": "group" if t in GROUP_TYPES else "person"} for k, n, t in who_info if n.strip() and (who_all or k in used_who)],
        "cat": [{"key": k, "label": n} for k, n in cat_info],
    }
    if note:
        src["note"] = note
    return src


def write_options(out, sources):
    write(os.path.join(out, "data/options.json"), json.dumps({"sources": list(sources)}, ensure_ascii=False, separators=(",", ":")))
    write(os.path.join(out, "custom/index.html"), PAGE)


PAGE = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>選んで作る RSS ｜ hello-rss</title>
<style>
:root{--bg:#fff;--fg:#222;--mute:#666;--line:#ddd;--accent:#0a66c2;--card:#f6f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mute:#999;--line:#333;--accent:#6cb2ff;--card:#202124}}
body{margin:0;padding:24px 16px 64px;background:var(--bg);color:var(--fg);font:16px/1.7 "Noto Sans JP",system-ui,sans-serif}
main{max-width:760px;margin:0 auto}h1{font-size:1.5rem;margin:0 0 4px}a{color:var(--accent)}
p,.mute{color:var(--mute);margin:4px 0}
fieldset{border:1px solid var(--line);border-radius:8px;margin:16px 0;padding:8px 14px 12px}
legend{font-weight:700;padding:0 6px}
label{display:inline-flex;align-items:center;gap:6px;margin:2px 14px 2px 0;cursor:pointer}
.grid{display:flex;flex-wrap:wrap}
details{margin-top:8px}summary{cursor:pointer;color:var(--mute)}
.out{background:var(--card);border-radius:8px;padding:12px 14px;margin-top:20px}
input[type=text]{width:100%;box-sizing:border-box;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:13px/1.4 ui-monospace,monospace}
.field{display:block;margin:8px 0 0;cursor:default}.field span{display:block}
button,.btn{display:inline-block;margin:8px 8px 0 0;padding:8px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer;text-decoration:none}
button.sub{background:none;color:var(--accent)}
ol{padding-left:1.3em;margin:6px 0}
</style></head><body><main>
<h1>選んで作る RSS</h1>
<p>サイトと種類を選んで、見たい人・グループ・カテゴリ・言葉を指定すると、その分だけのフィードのURLができます。何も選ばなければ、全部です。</p>
<p><a href="/">← フィードの一覧へ</a></p>
<fieldset><legend>1. サイトと種類</legend>
<div id="sources" class="grid"></div><p class="mute" id="note" hidden></p></fieldset>
<fieldset id="whoBox"><legend>2. 人・グループ</legend>
<div id="genBox"><span class="mute">全体のもの（特定の人・グループに紐づかないもの。全グループのタグが付いたもの、タグが付いていないもの）:</span><br>
<label><input type="radio" name="gen" value="" checked> 含める</label>
<label><input type="radio" name="gen" value="1"> 全体のものだけ</label>
<label><input type="radio" name="gen" value="0"> 全体のものを除く</label></div>
<div id="who"></div>
<button type="button" class="sub" id="clearWho">選択を外す</button></fieldset>
<fieldset id="catBox"><legend>3. カテゴリ</legend><div id="cat" class="grid"></div>
<button type="button" class="sub" id="clearCat">選択を外す</button></fieldset>
<fieldset><legend>4. 言葉（題名に含まれる言葉）</legend>
<label class="field" for="q"><span class="mute">含む言葉（スペースで区切ると、全部を含む記事）</span></label>
<input type="text" id="q" maxlength="60" placeholder="例: 譜久村" autocomplete="off">
<label class="field" for="x"><span class="mute">含まない言葉（スペースで区切ると、どれかを含む記事を除く）</span></label>
<input type="text" id="x" maxlength="60" placeholder="例: 写真集" autocomplete="off"></fieldset>
<div class="out"><strong>できたURL</strong>
<input type="text" id="url" readonly aria-label="できたURL">
<button type="button" id="copy">コピー</button><a class="btn sub" id="open" target="_blank" rel="noopener">開いて確かめる</a>
<span class="mute" id="msg" role="status"></span>
<p class="mute">最新の記事（確認用）</p><ol id="preview"></ol></div>
<script>
const $=id=>document.getElementById(id);
let SOURCES=[], cur=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
fetch('/data/options.json').then(r=>r.json()).then(o=>{SOURCES=o.sources;renderSources();pick(0)}).catch(()=>{$('msg').textContent='選択肢を読み込めませんでした'});
function renderSources(){
  $('sources').innerHTML=SOURCES.map((s,i)=>`<label><input type="radio" name="src" value="${i}"> ${esc(s.siteLabel)} ${esc(s.kindLabel)}</label>`).join('');
  document.querySelectorAll('input[name=src]').forEach(r=>r.addEventListener('change',()=>pick(+r.value)));
}
function box(name,o){return `<label><input type="checkbox" data-k="${esc(o.key)}" data-n="${name}"> ${esc(o.label)}</label>`}
function pick(i){
  cur=SOURCES[i]; document.querySelector(`input[name=src][value="${i}"]`).checked=true;
  const g=cur.who.filter(w=>w.type==='group'), p=cur.who.filter(w=>w.type!=='group');
  $('catBox').hidden=!cur.cat.length; $('q').value=''; $('x').value=''; document.querySelector('input[name=gen][value=""]').checked=true;
  $('note').hidden=!cur.note; $('note').textContent=cur.note||'';
  $('who').innerHTML=(g.length?`<div class="grid">${g.map(w=>box('who',w)).join('')}</div>`:'')+
    (p.length?`<details${g.length?'':' open'}><summary>${g.length?'メンバー・個人（'+p.length+'人）':'人'}</summary><div class="grid">${p.map(w=>box('who',w)).join('')}</div></details>`:'');
  $('cat').innerHTML=cur.cat.map(c=>box('cat',c)).join('');
  document.querySelectorAll('input[type=checkbox]').forEach(c=>c.addEventListener('change',update));
  setGeneral(); update();
}
// 「全体のものだけ」を選ぶと、人・グループは選べない（一緒には使えない）。「全体のものを除く」は、人・グループと一緒に使える
const gen=()=>document.querySelector('input[name=gen]:checked').value;
function setGeneral(){
  const on=gen()==='1';
  document.querySelectorAll('input[data-n=who]').forEach(c=>{if(on)c.checked=false;c.disabled=on});
  $('clearWho').disabled=on;
}
document.querySelectorAll('input[name=gen]').forEach(r=>r.addEventListener('change',()=>{setGeneral();update()}));
const checked=n=>[...document.querySelectorAll(`input[data-n=${n}]:checked`)].map(c=>c.dataset.k);
let timer;
function update(){
  const w=checked('who'), c=checked('cat'), q=$('q').value.trim(), x=$('x').value.trim();
  let u=location.origin+'/custom.xml?site='+cur.site+'&kind='+cur.kind;
  if(gen()==='1')u+='&general=1'; else{ if(w.length)u+='&who='+w.map(encodeURIComponent).join(','); if(gen()==='0')u+='&general=0'; }
  if(c.length)u+='&cat='+c.map(encodeURIComponent).join(',');
  if(q)u+='&q='+encodeURIComponent(q);
  if(x)u+='&x='+encodeURIComponent(x);
  $('url').value=u; $('open').href=u; $('msg').textContent='';
  clearTimeout(timer); timer=setTimeout(()=>preview(u),300);
}
let seq=0;
async function preview(u){
  const my=++seq;  // 遅れて返ってきた古い問い合わせの結果で、新しい結果を上書きしない
  $('preview').innerHTML='<li class="mute">確認中…</li>';
  try{
    const r=await fetch(u), body=await r.text(); if(my!==seq)return;
    if(!r.ok){$('preview').innerHTML='<li>'+esc(body)+'</li>';return}
    const xml=new DOMParser().parseFromString(body,'application/xml'), it=[...xml.querySelectorAll('item')];
    $('preview').innerHTML=it.length?it.slice(0,5).map(i=>'<li>'+esc(i.querySelector('title').textContent)+'</li>').join(''):'<li>該当する記事がありません</li>';
  }catch(e){if(my===seq)$('preview').innerHTML='<li>確認できませんでした</li>'}
}
$('q').addEventListener('input',update);
$('x').addEventListener('input',update);
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('url').value);$('msg').textContent='コピーしました'}catch(e){$('url').select();$('msg').textContent='選択しました。コピーしてください'}});
$('clearWho').addEventListener('click',()=>{document.querySelectorAll('input[data-n=who]').forEach(c=>c.checked=false);update()});
$('clearCat').addEventListener('click',()=>{document.querySelectorAll('input[data-n=cat]').forEach(c=>c.checked=false);update()});
</script></main></body></html>
"""

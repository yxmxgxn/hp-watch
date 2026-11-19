"""選択式フィード（/custom.xml?site=…&kind=…&who=…&cat=…）の元データと、選ぶ画面を作る

  data/{site}-{kind}.json … 記事（人・グループ・カテゴリ付き）。Worker（watcher/src/custom.js）が読んで、条件に合うものだけRSSにする
  data/options.json       … 選べるもの（サイト・種類・人／グループ・カテゴリ）。選ぶ画面と Worker が読む
  custom/index.html       … 選ぶ画面

方針: サイトはまたがない。お知らせとスケジュールは別。1本のフィード＝「1つのサイト × 片方の種類」の中で、人・グループとカテゴリを選ぶ。
"""
import datetime as dt
import json
import os

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


def make_source(out, site, site_label, kind, kind_label, items, who_info, cat_info, who_all=False):
    """元データを書いて、選ぶ画面用の選択肢を返す。
    items: "who"（キーのリスト）と "cats"（カテゴリのキーのリスト）が付いた記事
    who_info: [(キー, 名前, 種別)] をプロフィールの順に。cat_info: [(キー, 名前)] を画面に出す順に。
    名前が空のプロフィール（未発表のユニットなど）は出さない。カテゴリは、記事がまだ無くても全部出す。人・グループは、記事に出てくるものだけ（who_all なら全員。
    スケジュールは新しく載ったものから溜めるので、使い始めは記事が少なくても、見たい人を選べるようにする）"""
    rows = sorted(items, key=lambda i: (iso(i["date"]), str(i["sort"])), reverse=True)[:DATA_LIMIT]
    used_who = {k for i in rows for k in i.get("who") or []}
    data = {"items": [{
        "guid": i["guid"], "title": i["title"], "link": i["link"], "date": iso(i["date"]), "html": i["html"],
        "categories": i["categories"], "cats": i.get("cats") or [], "who": i.get("who") or [],
    } for i in rows]}
    file = f"/data/{site}-{kind}.json"
    write(os.path.join(out, file.lstrip("/")), json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    return {
        "site": site, "siteLabel": site_label, "kind": kind, "kindLabel": kind_label, "file": file, "count": len(rows),
        "who": [{"key": k, "label": n, "type": "group" if t in GROUP_TYPES else "person"} for k, n, t in who_info if n.strip() and (who_all or k in used_who)],
        "cat": [{"key": k, "label": n} for k, n in cat_info],
    }


# 公式が WordPress で RSS を出しているサイト。記事のデータは持たず、Worker が公式のRSSを取りに行って、タイトルだけにして返す。
# タグ・カテゴリの英字のIDは、公式の /tag/{ID}/・/category/{ID}/ のもの。新しいタグ・カテゴリが増えたら、ここに足す
HELLOSHOP = {
    "site": "helloshop", "siteLabel": "ハロショ", "kind": "news", "kindLabel": "新着情報（タイトルだけ）",
    "live": {"type": "wordpress", "base": "https://www.helloshop.info"},
    "keyword": True,  # 人名などのキーワードで探せる（公式の検索）。タイトルだけでなく、本文に含まれるものも出る
    "who": [{"key": k, "label": n, "type": "group"} for k, n in [
        ("morningmusume", "モーニング娘。'26"), ("angerme", "アンジュルム"), ("juicejuice", "Juice=Juice"),
        ("tsubakifactory", "つばきファクトリー"), ("beyooooonds", "BEYOOOOONDS"), ("ocha-norma", "OCHA NORMA"),
        ("rosychronicle", "ロージークロニクル"), ("sotsugyo", "ハロプロ卒業メンバー"), ("kensyu", "ハロプロ研修生"), ("other", "その他")]],
    "cat": [{"key": k, "label": n} for k, n in [
        ("news", "お知らせ"), ("event", "イベント"), ("goods", "グッズ情報"), ("photo", "生写真情報"),
        ("shop", "ショップ情報"), ("shops", "店舗"), ("extra", "臨時店")]],
}


def write_options(out, sources):
    sources = list(sources) + [HELLOSHOP]
    write(os.path.join(out, "data/options.json"), json.dumps({"sources": sources}, ensure_ascii=False, separators=(",", ":")))
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
button,.btn{display:inline-block;margin:8px 8px 0 0;padding:8px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer;text-decoration:none}
button.sub{background:none;color:var(--accent)}
ol{padding-left:1.3em;margin:6px 0}
</style></head><body><main>
<h1>選んで作る RSS</h1>
<p>サイトと種類を選んで、見たい人・グループとカテゴリにチェックを入れると、その分だけのフィードのURLができます。何も選ばなければ、全部です。</p>
<p><a href="/">← フィードの一覧へ</a></p>
<fieldset><legend>1. サイトと種類</legend>
<div id="sources" class="grid"></div></fieldset>
<fieldset id="whoBox"><legend>2. 人・グループ</legend><div id="who"></div>
<button type="button" class="sub" id="clearWho">選択を外す</button></fieldset>
<fieldset id="qBox" hidden><legend>キーワード</legend>
<label for="q" class="mute">人名など（記事のタイトルや本文に含まれる言葉。公式の検索を使います）</label>
<input type="text" id="q" maxlength="40" placeholder="例: 譜久村" autocomplete="off"></fieldset>
<fieldset id="catBox"><legend>3. カテゴリ</legend><div id="cat" class="grid"></div>
<button type="button" class="sub" id="clearCat">選択を外す</button></fieldset>
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
  $('whoBox').hidden=!cur.who.length; $('catBox').hidden=!cur.cat.length; $('qBox').hidden=!cur.keyword; $('q').value='';
  $('who').innerHTML=(g.length?`<div class="grid">${g.map(w=>box('who',w)).join('')}</div>`:'')+
    (p.length?`<details${g.length?'':' open'}><summary>${g.length?'メンバー・個人（'+p.length+'人）':'人'}</summary><div class="grid">${p.map(w=>box('who',w)).join('')}</div></details>`:'');
  $('cat').innerHTML=cur.cat.map(c=>box('cat',c)).join('');
  document.querySelectorAll('input[type=checkbox]').forEach(c=>c.addEventListener('change',update));
  update();
}
const checked=n=>[...document.querySelectorAll(`input[data-n=${n}]:checked`)].map(c=>c.dataset.k);
let timer;
function update(){
  const q=new URLSearchParams({site:cur.site,kind:cur.kind}), w=checked('who'), c=checked('cat');
  let u=location.origin+'/custom.xml?site='+cur.site+'&kind='+cur.kind;
  if(w.length)u+='&who='+w.map(encodeURIComponent).join(',');
  if(c.length)u+='&cat='+c.map(encodeURIComponent).join(',');
  const kw=$('q').value.trim(); if(cur.keyword&&kw)u+='&q='+encodeURIComponent(kw);
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
    const x=new DOMParser().parseFromString(body,'application/xml'), it=[...x.querySelectorAll('item')];
    $('preview').innerHTML=it.length?it.slice(0,5).map(i=>'<li>'+esc(i.querySelector('title').textContent)+'</li>').join(''):'<li>該当する記事がありません</li>';
  }catch(e){if(my===seq)$('preview').innerHTML='<li>確認できませんでした</li>'}
}
$('copy').addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('url').value);$('msg').textContent='コピーしました'}catch(e){$('url').select();$('msg').textContent='選択しました。コピーしてください'}});
$('q').addEventListener('input',update);
$('clearWho').addEventListener('click',()=>{document.querySelectorAll('input[data-n=who]').forEach(c=>c.checked=false);update()});
$('clearCat').addEventListener('click',()=>{document.querySelectorAll('input[data-n=cat]').forEach(c=>c.checked=false);update()});
</script></main></body></html>
"""

// ダッシュボード（鍵つき）: 放送の予定に、リンク（Tver・YouTube・Spotify など）・ゲスト名・メモを手で足す。自分だけが使う。
//   GET  /import         … 入力ページ。ページ自体には、データも鍵も無い（開けるが、何も出ない）
//   POST /import/state   … 鍵を確かめて、期限内の項目の一覧と、入っている中身を返す（鍵は本文に入れる）
//   POST /import/save    … 鍵を確かめて、manual/import.json をリポジトリに書く。そのあと rss.yml を呼んで、フィードに反映する
//
// 鍵は Worker の DASHBOARD_KEY（secret）。読み書きは GH_DATA_TOKEN（このリポジトリの contents だけ）。どちらも値はチャットに出さない。
// 期限: 放送日の3日後まで（rss.py の load_manual も同じ日で切る）。期限を過ぎた分は、保存のときにファイルから外す。

const FILE = "manual/import.json";
const AUTO_FILE = "manual/auto.json"; // tver.py が自動で入れた分（読むだけ）
const DAYS_AFTER = 3;   // 放送日から何日まで出すか
const WINDOW_DAYS = 10; // 一覧に出す放送日の前後（日）
const MAX_LINKS = 8;
const UA = "hello-rss-import";
const GUID = /^[a-z0-9-]+-media-[A-Za-z0-9_-]+$/;

const json = (status, body, extra = {}) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...extra } });

// ログイン（鍵を1回入れると、ブラウザに30日残る）。Cookie は、鍵から作った印（HMAC）。鍵を変えると、全部のログインが切れる
const COOKIE = "hr_import";
const SESSION_DAYS = 30;
const enc = (s) => new TextEncoder().encode(s);
async function sign(secret, msg) {
  const k = await crypto.subtle.importKey("raw", enc(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", k, enc(msg)));
  let bin = "";
  for (const b of sig) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
export async function makeSession(secret, nowMs) {
  const exp = String(nowMs + SESSION_DAYS * 86400e3);
  return `${exp}.${await sign(secret, "import:" + exp)}`;
}
export async function validSession(secret, token, nowMs) {
  if (!secret || typeof token !== "string") return false;
  const [exp, sig] = token.split(".");
  if (!exp || !sig || !/^\d+$/.test(exp) || Number(exp) < nowMs) return false;
  return sameKey(sig, await sign(secret, "import:" + exp));
}
export function cookieValue(header, name) {
  for (const part of String(header || "").split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === name) return part.slice(i + 1).trim();
  }
  return "";
}

// JST の日付（YYYY-MM-DD）と、その何日後か
export function jstDay(now) {
  return new Date(now.getTime() + 9 * 3600e3).toISOString().slice(0, 10);
}
export function addDays(day, n) {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const clean = (s) => String(s ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();

// 入力の形を確かめる。項目が実際にあるかは、呼び出し側で調べる
export function validate(body) {
  if (!body || typeof body !== "object") return { error: "入力が読めません" };
  const guid = String(body.guid || "").trim();
  if (!GUID.test(guid)) return { error: "項目のIDの形が違います" };
  const guest = clean(body.guest);
  if (guest.length > 60) return { error: "ゲスト名は60文字までです" };
  const note = clean(body.note);
  if (note.length > 200) return { error: "メモは200文字までです" };
  const raw = Array.isArray(body.links) ? body.links : [];
  if (raw.length > MAX_LINKS) return { error: `リンクは${MAX_LINKS}本までです` };
  const links = [];
  for (const l of raw) {
    const label = clean(l && l.label);
    if (!label || label.length > 20) return { error: "リンクの名前は1〜20文字にしてください" };
    const url = String(l && l.url || "").trim();
    if (url.length > 300) return { error: "URLが長すぎます" };
    let u;
    try {
      u = new URL(url);
    } catch {
      return { error: "URLの形が違います" };
    }
    if (u.protocol !== "https:") return { error: "URL は https:// で始めてください" };
    links.push({ label, url: u.toString() });
  }
  return { guid, guest, note, links };
}

// 前の形（tver だけ）の分も、リンクとして扱う
export function linksOf(e) {
  const out = [];
  if (e && e.tver) out.push({ label: "TVer", url: e.tver });
  for (const l of (e && e.links) || []) out.push(l);
  return out;
}

// 今のファイルに、1件を足す（同じ項目は入れ替え）。期限の切れた分は外す。中身が空なら、その項目は外すだけ
export function merge(file, entry, today) {
  const kept = (file && file.items || []).filter((e) => e.until >= today && e.guid !== entry.guid);
  if (entry.links.length || entry.guest || entry.note) kept.push(entry);
  kept.sort((a, b) => String(a.air).localeCompare(String(b.air)));
  return { items: kept };
}

// 鍵の比べ方: 両方をハッシュにしてから、全部の文字を比べる（途中で止めない）
async function sameKey(given, expected) {
  if (typeof given !== "string" || !expected) return false;
  const enc = new TextEncoder();
  const a = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(given)));
  const b = new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(expected)));
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

async function readAsset(env, path) {
  const r = await env.ASSETS.fetch(new Request(new URL(path, "https://assets.invalid")));
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

// フィードの元データ（data/）から、項目の放送日と名前を探す。無ければ null
async function findItem(env, guid) {
  const opts = await readAsset(env, "/data/options.json");
  for (const s of opts.sources || []) {
    if (!s.file) continue;
    const d = await readAsset(env, s.file);
    const it = (d.items || []).find((i) => i.guid === guid);
    if (it) return { air: String(it.air || "").slice(0, 10), name: it.name || it.title || "", site: s.siteLabel };
  }
  return null;
}

const ghHeaders = (env) => ({
  Authorization: `Bearer ${env.GH_DATA_TOKEN}`,
  Accept: "application/vnd.github+json",
  "X-GitHub-Api-Version": "2022-11-28",
  "User-Agent": UA,
});
const contentsUrl = (env, path = FILE) => `https://api.github.com/repos/${env.GH_REPO}/contents/${path}`;

async function readFile(env, path = FILE) {
  const r = await fetch(contentsUrl(env, path), { headers: ghHeaders(env) });
  if (r.status === 404) return { file: { items: [] }, sha: null };
  if (!r.ok) throw new Error(`GitHub ${r.status}（読み込み）`);
  const j = await r.json();
  const bytes = Uint8Array.from(atob(j.content.replace(/\n/g, "")), (c) => c.charCodeAt(0));
  return { file: JSON.parse(new TextDecoder().decode(bytes)), sha: j.sha };
}

async function writeFile(env, file, sha, message) {
  const bytes = new TextEncoder().encode(JSON.stringify(file, null, 1) + "\n");
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  const body = { message, content: btoa(bin), branch: "main" };
  if (sha) body.sha = sha;
  return fetch(contentsUrl(env), {
    method: "PUT",
    headers: { ...ghHeaders(env), "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// 鍵（本文に入れた場合）か、ログインの Cookie のどちらかが合えば通す
async function authorized(req, body, env, nowMs) {
  if (await sameKey(body && body.key, env.DASHBOARD_KEY)) return true;
  return validSession(env.DASHBOARD_KEY, cookieValue(req.headers.get("cookie"), COOKIE), nowMs);
}

// 期限内の項目（放送日の前後10日・ただし期限内）と、入っている中身
async function state(req, env, now) {
  let body;
  try { body = await req.json(); } catch { return json(400, { error: "入力が読めません" }); }
  if (!env.DASHBOARD_KEY) return json(500, { error: "鍵が設定されていません（DASHBOARD_KEY）" });
  if (!(await authorized(req, body, env, now.getTime()))) return json(403, { error: "ログインが切れています。鍵を入れてください" }, {});

  const today = jstDay(now);
  const lo = addDays(today, -DAYS_AFTER), hi = addDays(today, WINDOW_DAYS);
  const opts = await readAsset(env, "/data/options.json");
  const items = [];
  for (const s of opts.sources || []) {
    if (!s.file) continue; // アメブロ・YouTube は、データのファイルを持たない（保存せず、取りに行く）
    const d = await readAsset(env, s.file);
    for (const it of d.items || []) {
      if (!String(it.guid).includes("-media-") || !it.air) continue;
      const air = String(it.air).slice(0, 10);
      if (air < lo || air > hi) continue;
      items.push({ guid: it.guid, air, until: addDays(air, DAYS_AFTER), name: it.name || it.title || "", site: s.siteLabel, cat: String((it.cats && it.cats[0]) || "") });
    }
  }
  const cur = await readFile(env);
  const autoCur = await readFile(env, AUTO_FILE);
  const live = (f) => new Map(((f.file && f.file.items) || []).filter((e) => e.until >= today).map((e) => [e.guid, e]));
  const manual = live(cur), auto = live(autoCur);
  for (const it of items) {
    const e = manual.get(it.guid), a = auto.get(it.guid);
    if (e) {
      // 手で入れた分が優先。リンクが無いときだけ、自動のリンクを補う（rss.py と同じ）
      const own = linksOf(e);
      it.status = "入力済み";
      it.entry = { links: own.length ? own : linksOf(a), guest: e.guest || "", note: e.note || "", updated: e.updated || "", source: "manual" };
    } else if (a) {
      it.status = "自動";
      it.entry = { links: linksOf(a), guest: "", note: "", updated: a.found || "", source: "auto" };
    } else {
      it.status = "未入力";
      it.entry = null;
    }
  }
  items.sort((a, b) => a.air.localeCompare(b.air));
  return json(200, { today, items });
}

async function saveEntry(req, env, now, dispatch) {
  if (!env.DASHBOARD_KEY) return json(500, { error: "鍵が設定されていません（DASHBOARD_KEY）" });
  let body;
  try { body = await req.json(); } catch { return json(400, { error: "入力が読めません" }); }
  if (!(await authorized(req, body, env, now.getTime()))) return json(403, { error: "ログインが切れています。鍵を入れてください" }, {});

  const v = validate(body);
  if (v.error) return json(400, { error: v.error });
  const item = await findItem(env, v.guid);
  if (!item) return json(400, { error: "その項目が、フィードの元データに見つかりません" });
  if (!item.air) return json(400, { error: "放送日が分からない項目です（フィードを作り直すと、出るようになります）" });

  const entry = {
    guid: v.guid, air: item.air, until: addDays(item.air, DAYS_AFTER),
    links: v.links, guest: v.guest, note: v.note, updated: now.toISOString(),
  };
  const today = jstDay(now);
  for (let attempt = 0; attempt < 2; attempt++) {
    const cur = await readFile(env);
    const next = merge(cur.file, entry, today);
    const r = await writeFile(env, next, cur.sha, `ダッシュボード: ${item.name || v.guid} の情報を更新`);
    if (r.status === 200 || r.status === 201) {
      let note = "";
      try {
        await dispatch("rss.yml");
      } catch (e) {
        note = "（保存はできたが、フィードの作り直しを呼べなかった。次の定期実行で反映される）";
      }
      return json(200, { ok: true, until: entry.until, note });
    }
    if (r.status !== 409 && r.status !== 422) return json(502, { error: `GitHub ${r.status}（書き込み）` });
    // 409: ほかの書き込みと重なった。読み直してもう一度
  }
  return json(409, { error: "書き込みが重なりました。少し待って、もう一度保存してください" });
}

const PAGE = `<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>放送の情報を入れる</title>
<style>
:root{--bg:#fff;--fg:#222;--mute:#666;--line:#ddd;--accent:#0a66c2;--card:#f6f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mute:#999;--line:#333;--accent:#6cb2ff;--card:#202124}}
body{margin:0;padding:20px 16px 56px;background:var(--bg);color:var(--fg);font:15px/1.6 "Noto Sans JP",system-ui,sans-serif}
main{max-width:680px;margin:0 auto}h1{font-size:1.25rem;margin:0 0 6px}h2{font-size:1rem;margin:22px 0 6px}
p,.mute{color:var(--mute);margin:4px 0}
input,select{box-sizing:border-box;width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
label{display:block;margin:12px 0 4px;font-weight:700}
button{padding:8px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer;margin:6px 6px 0 0}
button.sub{background:none;color:var(--accent)}button:disabled{opacity:.5}
.row{display:flex;gap:8px;align-items:center}.row input[type=url]{flex:1}.row input[type=text]{width:130px;flex:none}
.list{border:1px solid var(--line);border-radius:8px;margin-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chips button{margin:0;padding:5px 12px;border-radius:14px;background:none;color:var(--accent)}
.chips button.on{background:var(--accent);color:#fff}
.item{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-top:1px solid var(--line)}
.item:first-child{border-top:0}.item .main{flex:1;min-width:0}.item .name{font-weight:700;word-break:break-all}
.badge{display:inline-block;font-size:.8rem;padding:1px 8px;border-radius:10px;border:1px solid var(--line);color:var(--mute);margin-left:6px}
.badge.done{border-color:var(--accent);color:var(--accent)}
.got{font-size:.9rem;color:var(--mute);margin-top:2px;word-break:break-all}
#form{border:1px solid var(--line);border-radius:8px;padding:12px;margin-top:14px;background:var(--card)}
#msg{font-weight:700;margin-top:10px}.ok{color:#1a7f37}.ng{color:#c62828}
</style></head><body><main>
<h1>放送の情報を入れる</h1>
<p class="mute">リンクや、ゲスト・メモを入れると、放送日の3日後まで、フィードの記事の後ろに付きます。ページには、鍵を入れるまで何も出ません。</p>

<label for="key">鍵</label>
<div class="row"><input id="key" type="password" autocomplete="current-password"><button id="login" type="button">ログイン</button></div>
<p class="mute">鍵は、ログインのときだけ入れます。ログインは30日残ります。</p>
<div class="row"><button id="load" type="button" class="sub">一覧を読む</button><button id="logout" type="button" class="sub">ログアウト</button></div>
<p id="msg" role="status"></p>

<h2>期限内の項目</h2>
<div class="chips" id="filters"></div>
<p class="mute" id="count"></p>
<div class="list" id="list"></div>

<div id="form" hidden>
  <p><strong id="fname"></strong><span class="mute" id="fmeta"></span></p>
  <input id="guid" type="hidden">
  <label>リンク（TVer・YouTube・Spotify など）</label>
  <div id="links"></div>
  <button class="sub" id="addLink" type="button">＋ リンク</button>
  <label for="guest">ゲスト（任意）</label>
  <input id="guest" maxlength="60">
  <label for="note">メモ（任意・フィードに出ます）</label>
  <input id="note" maxlength="200">
  <p class="mute">保存すると、フィードの記事の後ろに、この内容が付きます（次の作り直しで反映）。</p>
  <button id="save" type="button">保存</button>
  <button class="sub" id="clear" type="button">この項目の情報を消す</button>
</div>

<datalist id="labels"><option value="TVer"><option value="YouTube"><option value="Spotify"><option value="公式"><option value="記事"></datalist>

<script>
const $ = (id) => document.getElementById(id);
const KEY = () => $("key").value;
let items = [];
const msg = (t, cls) => { $("msg").className = cls || ""; $("msg").textContent = t; };
const plus = (day, n) => { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const post = async (path, body) => {
  const r = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  return { ok: r.ok, status: r.status, j };
};

function linkRow(label, url) {
  const row = document.createElement("div");
  row.className = "row"; row.style.marginTop = "6px";
  const l = document.createElement("input"); l.type = "text"; l.placeholder = "名前"; l.setAttribute("list", "labels"); l.maxLength = 20; l.value = label || "";
  const u = document.createElement("input"); u.type = "url"; u.placeholder = "https://..."; u.maxLength = 300; u.value = url || "";
  const d = document.createElement("button"); d.type = "button"; d.className = "sub"; d.textContent = "削除";
  d.addEventListener("click", () => row.remove());
  row.append(l, u, d);
  return row;
}

function fill(it) {
  $("form").hidden = false;
  $("fname").textContent = it.name;
  $("fmeta").textContent = "  " + it.air + " 放送（" + it.site + "）・期限 " + it.until;
  $("guid").value = it.guid;
  $("guest").value = it.entry ? it.entry.guest : "";
  $("note").value = it.entry ? it.entry.note : "";
  $("links").textContent = "";
  ((it.entry && it.entry.links) || []).forEach((l) => $("links").appendChild(linkRow(l.label, l.url)));
  if (!$("links").children.length) $("links").appendChild(linkRow("", ""));
  msg(it.entry && it.entry.source === "auto" ? "自動で入ったリンクです。直して保存すると、手で入れた分が優先されます" : "");
  $("form").scrollIntoView({ behavior: "smooth", block: "start" });
}

const CATS = [["", "すべて"], ["TV", "テレビ"], ["RADIO", "ラジオ"], ["MAGAZINE", "雑誌"], ["WEB", "WEB"], ["OTHER", "その他"]];
let filter = "";
const MEDIA = ["TV", "RADIO", "MAGAZINE", "WEB"];
const matches = (it) => !filter || (filter === "OTHER" ? !MEDIA.includes(it.cat) : it.cat === filter);
function drawFilters() {
  const box = $("filters");
  box.textContent = "";
  CATS.forEach(([key, label]) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.className = key === filter ? "on" : "";
    b.addEventListener("click", () => { filter = key; drawFilters(); renderList(); });
    box.appendChild(b);
  });
}

function renderList() {
  const list = $("list");
  list.textContent = "";
  const shown = items.filter(matches);
  $("count").textContent = !items.length ? "期限内の項目はありません"
    : (shown.length === items.length ? items.length + "件（放送日の前後。期限内のもの）" : shown.length + "件（全" + items.length + "件のうち）");
  shown.forEach((it) => {
    const row = document.createElement("div");
    row.className = "item";
    const main = document.createElement("div"); main.className = "main";
    const name = document.createElement("div"); name.className = "name"; name.textContent = it.name;
    const badge = document.createElement("span"); badge.className = "badge" + (it.entry ? " done" : ""); badge.textContent = it.status;
    name.appendChild(badge);
    const meta = document.createElement("div"); meta.className = "mute"; meta.textContent = it.air + " 放送（" + it.site + "）・期限 " + it.until;
    main.append(name, meta);
    if (it.entry) {
      const got = document.createElement("div"); got.className = "got";
      const parts = it.entry.links.map((l) => l.label);
      if (it.entry.guest) parts.push("ゲスト: " + it.entry.guest);
      if (it.entry.note) parts.push("メモ: " + it.entry.note);
      got.textContent = (it.entry.source === "auto" ? "自動で入った: " : "入っている: ") + parts.join(" / ") + (it.entry.updated ? "（" + it.entry.updated.slice(0, 16).replace("T", " ") + "）" : "");
      main.appendChild(got);
    }
    const btn = document.createElement("button"); btn.type = "button"; btn.className = "sub"; btn.textContent = it.entry ? "編集" : "入れる";
    btn.addEventListener("click", () => fill(it));
    row.append(main, btn);
    list.appendChild(row);
  });
}

async function loadList(quiet) {
  try {
    const r = await post("/import/state", {});
    if (!r.ok) {
      if (!quiet) msg(r.status === 403 ? "ログインが切れています。鍵を入れて「ログイン」を押してください" : (r.j.error || "読めませんでした（" + r.status + "）"), "ng");
      return false;
    }
    items = r.j.items || [];
    renderList();
    if (!quiet) msg("");
    return true;
  } catch (e) {
    if (!quiet) msg("通信できませんでした", "ng");
    return false;
  }
}

$("login").addEventListener("click", async () => {
  $("login").disabled = true;
  try {
    const r = await post("/import/login", { key: KEY() });
    if (!r.ok) { msg(r.j.error || "ログインできませんでした（" + r.status + "）", "ng"); return; }
    $("key").value = "";
    msg("ログインしました");
    await loadList(false);
  } catch (e) {
    msg("通信できませんでした", "ng");
  } finally {
    $("login").disabled = false;
  }
});

$("load").addEventListener("click", () => loadList(false));
drawFilters();

$("logout").addEventListener("click", async () => {
  await post("/import/logout", {});
  items = []; renderList(); $("form").hidden = true;
  $("count").textContent = "";
  msg("ログアウトしました");
});

$("addLink").addEventListener("click", () => $("links").appendChild(linkRow("", "")));

function collect() {
  const links = [];
  for (const row of $("links").children) {
    const [l, u] = row.querySelectorAll("input");
    if (!l.value.trim() && !u.value.trim()) continue;
    links.push({ label: l.value.trim(), url: u.value.trim() });
  }
  return links;
}

async function save(clearing) {
  const guid = $("guid").value;
  if (!guid) { msg("項目を選んでください", "ng"); return; }
  const body = clearing
    ? { guid, links: [], guest: "", note: "" }
    : { guid, links: collect(), guest: $("guest").value.trim(), note: $("note").value.trim() };
  $("save").disabled = true; $("clear").disabled = true;
  msg("保存しています…");
  try {
    const r = await post("/import/save", body);
    if (!r.ok) { msg(r.j.error || "失敗しました（" + r.status + "）", "ng"); return; }
    msg("保存しました。" + (r.j.note || "フィードに反映されるまで、少し待ってください"), "ok");
    await loadList(true);
  } catch (e) {
    msg("通信できませんでした", "ng");
  } finally {
    $("save").disabled = false; $("clear").disabled = false;
  }
}
$("save").addEventListener("click", () => save(false));
loadList(true);
$("clear").addEventListener("click", () => { if (confirm("この項目の情報を消しますか？")) save(true); });
</script>
</main></body></html>
`;

// Worker の入口から呼ぶ。対象外なら null を返す
export async function importRoutes(req, env, { now, dispatch }) {
  const url = new URL(req.url);
  if (req.method === "GET" && url.pathname === "/import") {
    return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  }
  if (req.method === "POST" && url.pathname === "/import/login") return guard("login", () => login(req, env, now));
  if (req.method === "POST" && url.pathname === "/import/logout") return json(200, { ok: true }, { "set-cookie": `${COOKIE}=; Path=/import; Max-Age=0; HttpOnly; Secure; SameSite=Strict` });
  if (req.method === "POST" && url.pathname === "/import/state") return guard("state", () => state(req, env, now));
  if (req.method === "POST" && url.pathname === "/import/save") return guard("save", () => saveEntry(req, env, now, dispatch));
  return null;
}

async function login(req, env, now) {
  if (!env.DASHBOARD_KEY) return json(500, { error: "鍵が設定されていません（DASHBOARD_KEY）" });
  let body;
  try { body = await req.json(); } catch { return json(400, { error: "入力が読めません" }); }
  if (!(await sameKey(body && body.key, env.DASHBOARD_KEY))) return json(403, { error: "鍵が違います" });
  const tok = await makeSession(env.DASHBOARD_KEY, now.getTime());
  return json(200, { ok: true }, { "set-cookie": `${COOKIE}=${tok}; Path=/import; Max-Age=${SESSION_DAYS * 86400}; HttpOnly; Secure; SameSite=Strict` });
}

// 途中で例外が出ても、画面に読める理由を返す（GitHub の応答の番号など。鍵や中身は出さない）
async function guard(name, fn) {
  try {
    return await fn();
  } catch (e) {
    const why = (e && e.message) || String(e);
    console.error(`import ${name}: ${why}`);
    return json(502, { error: `${name === "state" ? "一覧" : "保存"}で失敗しました: ${why}` });
  }
}

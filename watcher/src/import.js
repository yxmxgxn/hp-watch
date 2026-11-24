// ダッシュボード（鍵つき）: 放送の予定に、リンク（Tver・YouTube・Spotify など）・ゲスト名・メモを手で足す。自分だけが使う。
//   GET  /import         … 入力ページ。ページ自体には、データも鍵も無い（開けるが、何も出ない）
//   POST /import/state   … 鍵を確かめて、期限内の項目の一覧と、入っている中身を返す（鍵は本文に入れる）
//   POST /import/save    … 鍵を確かめて、manual/import.json をリポジトリに書く。そのあと rss.yml を呼んで、フィードに反映する
//
// 鍵は Worker の DASHBOARD_KEY（secret）。読み書きは GH_DATA_TOKEN（このリポジトリの contents だけ）。どちらも値はチャットに出さない。
// 期限: 放送日の3日後まで（rss.py の load_manual も同じ日で切る）。期限を過ぎた分は、保存のときにファイルから外す。

const FILE = "manual/import.json";
const AUTO_FILE = "manual/auto.json"; // tver.py が自動で入れた分（読むだけ）
const EPISODES_FILE = "manual/auto_episodes.json"; // episodes.py が自動で入れた分（ポッドキャスト・YouTube。読むだけ）
const LATEST_FILE = "manual/episodes.json"; // 番組ごとの最新のエピソード（episodes.py。読むだけ）
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

// 放送の開始が、もう過ぎたか（開始は "25:30" のように24時を超えることがある。開始が無ければ、日付だけで見る）
export function started(air, start, now) {
  const today = jstDay(now);
  if (air > today) return false;
  const m = /^(\d{1,2}):(\d{2})/.exec(start || "");
  if (!m) return true;
  const jst = new Date(now.getTime() + 9 * 3600e3);
  const days = Math.round((Date.parse(today + "T00:00:00Z") - Date.parse(air + "T00:00:00Z")) / 86400e3);
  return days * 1440 + jst.getUTCHours() * 60 + jst.getUTCMinutes() >= Number(m[1]) * 60 + Number(m[2]);
}
const RADIKO_DAYS = 7; // radiko のタイムフリーは、放送から1週間まで

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

async function readOptional(env, path) {
  try {
    return await readAsset(env, path);
  } catch {
    return null;
  }
}

// フィードの元データ（data/）から、項目の放送日と名前を探す。無ければ null。毎週・毎月の番組の1回分（data/weekly.json）も探す
async function findItem(env, guid) {
  const opts = await readAsset(env, "/data/options.json");
  for (const s of opts.sources || []) {
    if (!s.file) continue;
    const d = await readAsset(env, s.file);
    const it = (d.items || []).find((i) => i.guid === guid);
    if (it) return { air: String(it.air || "").slice(0, 10), name: it.name || it.title || "", site: s.siteLabel };
  }
  const w = (((await readOptional(env, "/data/weekly.json")) || {}).items || []).find((i) => i.guid === guid);
  if (w) return { air: w.air, name: weeklyName(w), site: w.siteLabel };
  return null;
}

const weeklyName = (w) => w.prog + (w.station ? `（${w.station}）` : "");

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

// 期限内の項目（放送日の前後10日・ただし期限内）と、入っている中身。毎週・毎月の番組は、放送後3日の分を同じ一覧に「毎週」の印で出し、
// 番組ごとにまとめた見方（weekly）も返す（放送の翌日に、リンクが付いているかを見る）
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
      items.push({ guid: it.guid, air, until: addDays(air, DAYS_AFTER), name: it.name || it.title || "", site: s.siteLabel, cat: String((it.cats && it.cats[0]) || ""), weekly: false });
    }
  }
  const cur = await readFile(env);
  const autoCur = await readFile(env, AUTO_FILE);
  const epsCur = await readFile(env, EPISODES_FILE);
  const live = (f) => new Map(((f.file && f.file.items) || []).filter((e) => e.until >= today).map((e) => [e.guid, e]));
  const manual = live(cur), auto = live(autoCur);
  for (const [guid, e] of live(epsCur)) {
    const a = auto.get(guid);
    auto.set(guid, a ? { ...a, links: [...(a.links || []), ...(e.links || [])], found: a.found || e.found } : e);
  }
  const resolve = (guid) => {
    const e = manual.get(guid), a = auto.get(guid);
    if (e) {
      // 手で入れた分が優先。リンクが無いときだけ、自動のリンクを補う（rss.py と同じ）
      const own = linksOf(e);
      return { status: "入力済み", entry: { links: own.length ? own : linksOf(a), guest: e.guest || "", note: e.note || "", updated: e.updated || "", source: "manual" } };
    }
    if (a) return { status: "自動", entry: { links: linksOf(a), guest: "", note: "", updated: a.found || "", source: "auto" } };
    return { status: "未入力", entry: null };
  };

  // 毎週・毎月の番組の1回分ずつ。放送後3日までの分は、一覧にも出す
  const weeklyAll = ((await readOptional(env, "/data/weekly.json")) || {}).items || [];
  for (const w of weeklyAll) {
    if (w.air < lo || w.air > today) continue;
    items.push({ guid: w.guid, air: w.air, until: addDays(w.air, DAYS_AFTER), name: weeklyName(w), site: w.siteLabel, cat: w.cat, weekly: true, radiko: w.radiko || [], base: w.base || [] });
  }
  for (const it of items) Object.assign(it, resolve(it.guid));
  items.sort((a, b) => a.air.localeCompare(b.air) || a.name.localeCompare(b.name));

  // 番組ごとの行き先（Spotify・ポッドキャスト・YouTube）と、最新のエピソード。公式の予定に枠が無い番組（ポッドキャストだけの番組）も、ここで見る
  const srcList = (await readOptional(env, "/data/sources.json")) || [];
  const latestAll = ((await readFile(env, LATEST_FILE)).file || {}).programs || {};
  const norm = (x) => String(x || "").normalize("NFKC").toLowerCase().replace(/[\s\p{P}\p{S}_]+/gu, "");
  const srcKeys = srcList.map((src) => ({ src, keys: (src.match || []).map(norm).filter(Boolean) }));
  // 毎週タブ: 番組ごとに、前後の1回分ずつと、リンクが付いているか
  const groups = new Map();
  for (const w of weeklyAll) {
    const r = resolve(w.guid);
    const begun = started(w.air, w.start, now); // 放送が始まるまでは、タイムフリーのリンクがあっても『これから』
    const labels = [...(begun ? (w.radiko || []).map((l) => l[0]) : []), ...((r.entry && r.entry.links) || []).map((l) => l.label)];
    const expected = w.cat === "MAGAZINE" ? false : true; // 雑誌・連載は、リンクを期待しない
    const hasLink = labels.length > 0 || (r.entry && (r.entry.guest || r.entry.note));
    // ポッドキャスト・YouTube が付く番組は、1週間を過ぎても、あとからリンクが付くことがある
    const later = srcKeys.some(({ src, keys }) => (src.podcast || src.youtube) && keys.some((k) => norm(w.prog).includes(k)));
    let state_ = "ok";
    if (!begun) state_ = "upcoming";
    else if (!hasLink) {
      if (!expected) state_ = "none";
      else if (w.cat === "RADIO" && !later && w.air < addDays(today, -RADIKO_DAYS)) continue; // radiko のタイムフリーの期限を過ぎて、ほかに付く先も無い回は、出さない
      else state_ = w.air >= addDays(today, -1) ? "pending" : "missing";
    }
    const key = `${w.siteLabel}|${w.cat}|${w.prog}`;
    if (!groups.has(key)) groups.set(key, { key, prog: w.prog, cat: w.cat, siteLabel: w.siteLabel, label: w.label, base: w.base || [], occurrences: [] });
    groups.get(key).occurrences.push({ guid: w.guid, air: w.air, start: w.start, station: w.station, state: state_, labels, status: r.status, entry: r.entry, site: w.siteLabel, name: weeklyName(w) });
  }
  const sources = srcKeys.map(({ src, keys }) => {
    const occ = weeklyAll.filter((w) => keys.some((k) => norm(w.prog).includes(k)));
    const eps = latestAll[src.name] || [];
    const newest = eps.length ? eps[0].date : "";
    return { name: src.name, spotify: src.spotify || "", youtube: src.youtube || "", podcast: !!src.podcast, hasSchedule: occ.length > 0, episodes: eps, newest,
             stale: !!src.podcast && (!newest || newest < addDays(today, -8)) };
  });
  const weekly = [...groups.values()];
  for (const g of weekly) g.occurrences.sort((a, b) => a.air.localeCompare(b.air) || a.start.localeCompare(b.start));
  weekly.sort((a, b) => a.cat.localeCompare(b.cat) || a.prog.localeCompare(b.prog));
  return json(200, { today, items, weekly, sources });
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
:root{--bg:#fff;--fg:#222;--mute:#666;--line:#ddd;--accent:#0080cb;--link:#0074bb;--card:#f6f7f9}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mute:#999;--line:#333;--accent:#0080cb;--link:#55b4ee;--card:#202124}}
body{margin:0;padding:20px 16px 56px;background:var(--bg);color:var(--fg);font:15px/1.6 "Noto Sans JP",system-ui,sans-serif}
main{max-width:680px;margin:0 auto}h1{font-size:1.25rem;margin:0 0 6px}h2{font-size:1rem;margin:22px 0 6px}
p,.mute{color:var(--mute);margin:4px 0}
input,select{box-sizing:border-box;width:100%;padding:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
label{display:block;margin:12px 0 4px;font-weight:700}
button{padding:8px 14px;border:1px solid var(--accent);border-radius:6px;background:var(--accent);color:#fff;font:inherit;cursor:pointer;margin:6px 6px 0 0}
button.sub{background:none;color:var(--link)}button:disabled{opacity:.5}
.row{display:flex;gap:8px;align-items:center}.row input[type=url]{flex:1}.row input[type=text]{width:130px;flex:none}
.list{border:1px solid var(--line);border-radius:8px;margin-top:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chips button{margin:0;padding:5px 12px;border-radius:14px;background:none;color:var(--link)}
.chips button.on{background:var(--accent);color:#fff}
.item{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-top:1px solid var(--line)}
.item:first-child{border-top:0}.item .main{flex:1;min-width:0}.item .name{font-weight:700;word-break:break-all}
.badge{display:inline-block;font-size:.8rem;padding:1px 8px;border-radius:10px;border:1px solid var(--line);color:var(--mute);margin-left:6px}
.badge.done{border-color:var(--accent);color:var(--link)}
.got{font-size:.9rem;color:var(--mute);margin-top:2px;word-break:break-all}
.tabs{display:flex;gap:6px;margin-top:14px}.tabs button{margin:0;border-radius:6px 6px 0 0}.tabs button:not(.on){background:none;color:var(--link)}
.card{border:1px solid var(--line);border-radius:8px;margin-top:10px;padding:8px 12px}.card .t{font-weight:700;word-break:break-all}
.occ{display:flex;gap:8px;align-items:baseline;padding:3px 0;border-top:1px solid var(--line);font-size:.92rem;cursor:pointer}.occ:first-of-type{border-top:0}
.occ .d{width:8.4em;flex:none;color:var(--mute)}.occ .s{flex:1;min-width:0;word-break:break-all}.occ .m{flex:none;font-weight:700}
.st-ok{color:#1a7f37}.st-pending{color:#b26a00}.st-missing{color:#c62828}.st-upcoming,.st-none{color:var(--mute)}
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

<div class="tabs"><button id="tabList" type="button">期限内</button><button id="tabWeekly" type="button">毎週</button></div>
<div id="listBox">
<h2>期限内の項目</h2>
<div class="chips" id="filters"></div>
<p class="mute" id="count"></p>
<div class="list" id="list"></div>
</div>
<div id="weeklyBox" hidden>
<h2>毎週・毎月の番組</h2>
<p class="mute" id="wcount"></p>
<div id="wlist"></div>
<h2>番組ごとの行き先（Spotify・ポッドキャスト・YouTube）</h2>
<p class="mute" id="scount"></p>
<div id="slist"></div>
</div>

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
let weekly = [];
let sources = [];
let tab = "list";
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

const CATS = [["", "すべて"], ["WEEKLY", "毎週"], ["TV", "テレビ"], ["RADIO", "ラジオ"], ["MAGAZINE", "雑誌"], ["WEB", "WEB"], ["OTHER", "その他"]];
let filter = "";
const MEDIA = ["TV", "RADIO", "MAGAZINE", "WEB"];
const matches = (it) => !filter || (filter === "WEEKLY" ? it.weekly : filter === "OTHER" ? !MEDIA.includes(it.cat) : it.cat === filter);
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

const STATE = { ok: ["✓", "反映済み"], pending: ["…", "待ち"], missing: ["✗", "未反映"], upcoming: ["○", "これから"], none: ["－", ""] };
const md2 = (d) => d.slice(5).replace("-", "/");
function renderWeekly() {
  const box = $("wlist");
  box.textContent = "";
  let miss = 0, pend = 0, ok = 0;
  weekly.forEach((g) => {
    g.occurrences.forEach((o) => { if (o.state === "missing") miss++; else if (o.state === "pending") pend++; else if (o.state === "ok") ok++; });
    const card = document.createElement("div"); card.className = "card";
    const t = document.createElement("div"); t.className = "t";
    t.textContent = g.prog;
    const b = document.createElement("span"); b.className = "badge"; b.textContent = g.cat + "・" + g.siteLabel + (g.label ? "・" + g.label : "");
    t.appendChild(b);
    card.appendChild(t);
    if (g.base && g.base.length) {
      const bl = document.createElement("div"); bl.className = "got"; bl.textContent = "最初から入っている: " + g.base.map((l) => l[0]).join(" / ");
      card.appendChild(bl);
    }
    g.occurrences.forEach((o) => {
      const row = document.createElement("div"); row.className = "occ";
      const d = document.createElement("span"); d.className = "d"; d.textContent = md2(o.air) + " " + (o.start || "");
      const s = document.createElement("span"); s.className = "s"; s.textContent = (o.station || "") + (o.labels.length ? "  " + o.labels.join(" / ") : "");
      const m = document.createElement("span"); m.className = "m st-" + o.state; m.textContent = STATE[o.state][0] + " " + STATE[o.state][1];
      row.append(d, s, m);
      row.addEventListener("click", () => fill({ guid: o.guid, name: o.name, air: o.air, until: plus(o.air, 3), site: o.site, entry: o.entry }));
      card.appendChild(row);
    });
    box.appendChild(card);
  });
  $("wcount").textContent = weekly.length
    ? weekly.length + "番組（前後の1回分ずつ）。反映済み " + ok + "・待ち " + pend + "・未反映 " + miss + "（放送の翌日までにリンクが付いていないもの）"
    : "毎週・毎月の番組は、まだありません（フィードを作り直すと出ます）";
}

function renderSources() {
  const box = $("slist");
  box.textContent = "";
  let stale = 0;
  sources.forEach((src) => {
    if (src.stale) stale++;
    const card = document.createElement("div"); card.className = "card";
    const t = document.createElement("div"); t.className = "t"; t.textContent = src.name;
    const b = document.createElement("span"); b.className = "badge"; b.textContent = src.hasSchedule ? "公式の予定に枠あり" : "公式の予定に枠なし";
    t.appendChild(b);
    card.appendChild(t);
    const links = document.createElement("div"); links.className = "got";
    const add = (label, url) => { if (!url) return; const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; a.textContent = label; links.appendChild(a); links.appendChild(document.createTextNode("  ")); };
    add("Spotify（番組）", src.spotify);
    add("YouTube（チャンネル）", src.youtube);
    if (!src.spotify && !src.youtube) links.textContent = "";
    if (links.childNodes.length) card.appendChild(links);
    const st = document.createElement("div"); st.className = "occ";
    if (src.podcast) {
      const m = document.createElement("span"); m.className = "m " + (src.stale ? "st-missing" : "st-ok");
      m.textContent = src.stale ? "✗ 8日以上、新しいエピソードが無い" : "✓ ポッドキャストあり";
      st.appendChild(m);
    } else if (src.youtube) {
      st.textContent = "YouTube は、放送日に公開された動画を、自動で結びつける"; st.style.color = "var(--mute)";
    } else {
      st.textContent = "ポッドキャストの公開RSSなし（Spotify の番組のページだけ）"; st.style.color = "var(--mute)";
    }
    card.appendChild(st);
    (src.episodes || []).slice(0, 3).forEach((e) => {
      const row = document.createElement("div"); row.className = "occ";
      const d = document.createElement("span"); d.className = "d"; d.textContent = md2(String(e.date || "").slice(0, 10));
      const s = document.createElement("span"); s.className = "s";
      if (e.link) { const a = document.createElement("a"); a.href = e.link; a.target = "_blank"; a.rel = "noopener"; a.textContent = e.title; s.appendChild(a); } else s.textContent = e.title;
      row.append(d, s);
      card.appendChild(row);
    });
    box.appendChild(card);
  });
  $("scount").textContent = sources.length ? sources.length + "番組。新しいエピソードが8日以上無いもの: " + stale : "番組ごとの行き先は、まだありません";
}

function showTab(t) {
  tab = t;
  $("listBox").hidden = t !== "list";
  $("weeklyBox").hidden = t !== "weekly";
  $("tabList").style.opacity = t === "list" ? "1" : ".6";
  $("tabWeekly").style.opacity = t === "weekly" ? "1" : ".6";
}
$("tabList").addEventListener("click", () => showTab("list"));
$("tabWeekly").addEventListener("click", () => showTab("weekly"));

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
    if (it.weekly) { const w = document.createElement("span"); w.className = "badge"; w.textContent = "毎週"; name.appendChild(w); }
    const meta = document.createElement("div"); meta.className = "mute"; meta.textContent = it.air + " 放送（" + it.site + "）・期限 " + it.until;
    main.append(name, meta);
    const auto = [...(it.radiko || []).map((l) => l[0]), ...(it.base || []).map((l) => l[0])];
    if (auto.length) {
      const a = document.createElement("div"); a.className = "got"; a.textContent = "最初から入っている: " + auto.join(" / ");
      main.appendChild(a);
    }
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
    weekly = r.j.weekly || [];
    sources = r.j.sources || [];
    renderList();
    renderWeekly();
    renderSources();
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
showTab("list");

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

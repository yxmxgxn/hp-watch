// ダッシュボード（鍵つき）: 放送の予定に、Tver のリンクやゲスト名を手で足す。自分だけが使う。
//   GET  /import       … 入力ページ（鍵・項目・Tver のURL・ゲスト名）。データは public の data/ から読む（公開のもの）
//   POST /import/save  … 鍵を確かめて、manual/import.json をリポジトリに書く。そのあと rss.yml を呼んで、フィードに反映する
//
// 鍵は Worker の DASHBOARD_KEY（secret）。書き込みは GH_DATA_TOKEN（このリポジトリの contents だけ）。どちらも値はチャットに出さない。
// 期限: 放送日の3日後まで（rss.py の load_manual も同じ日で切る）。期限を過ぎた分は、次の保存のときにファイルから外す。

const FILE = "manual/import.json";
const DAYS_AFTER = 3;
const UA = "hello-rss-import";
const GUID = /^[a-z0-9-]+-media-[A-Za-z0-9_-]+$/;
const TVER = /^https:\/\/tver\.jp\/(episodes|series)\/[A-Za-z0-9]+(\?[^\s]*)?$/;

const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });

// JST の日付（YYYY-MM-DD）と、その何日後か
export function jstDay(now) {
  return new Date(now.getTime() + 9 * 3600e3).toISOString().slice(0, 10);
}
export function addDays(day, n) {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

// 入力の形を確かめる。項目が実際にあるかは、呼び出し側で調べる
export function validate(body) {
  if (!body || typeof body !== "object") return { error: "入力が読めません" };
  const guid = String(body.guid || "").trim();
  if (!GUID.test(guid)) return { error: "項目のIDの形が違います" };
  const tver = String(body.tver || "").trim();
  if (tver && (tver.length > 200 || !TVER.test(tver))) return { error: "Tver のURLは、https://tver.jp/episodes/… か /series/… の形にしてください" };
  const guest = String(body.guest || "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  if (guest.length > 60) return { error: "ゲスト名は60文字までです" };
  return { guid, tver, guest };
}

// 今のファイルに、1件を足す（同じ項目は入れ替え）。期限の切れた分は外す。両方空なら、その項目は外すだけ
export function merge(file, entry, today) {
  const kept = (file && file.items || []).filter((e) => e.until >= today && e.guid !== entry.guid);
  if (entry.tver || entry.guest) kept.push(entry);
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
const contentsUrl = (env) => `https://api.github.com/repos/${env.GH_REPO}/contents/${FILE}`;

async function readFile(env) {
  const r = await fetch(contentsUrl(env), { headers: ghHeaders(env) });
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

async function save(req, env, now, dispatch) {
  if (!env.DASHBOARD_KEY) return json(500, { error: "鍵が設定されていません（DASHBOARD_KEY）" });
  let body;
  try {
    body = await req.json();
  } catch {
    return json(400, { error: "入力が読めません" });
  }
  if (!(await sameKey(body && body.key, env.DASHBOARD_KEY))) return json(403, { error: "鍵が違います" });

  const v = validate(body);
  if (v.error) return json(400, { error: v.error });
  const item = await findItem(env, v.guid);
  if (!item) return json(400, { error: "その項目が、フィードの元データに見つかりません" });
  if (!item.air) return json(400, { error: "放送日が分からない項目です（フィードを作り直すと、出るようになります）" });

  const entry = { guid: v.guid, air: item.air, until: addDays(item.air, DAYS_AFTER), tver: v.tver, guest: v.guest, updated: now.toISOString() };
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
:root{--bg:#fff;--fg:#222;--mute:#666;--line:#ddd;--accent:#0a66c2}
@media (prefers-color-scheme:dark){:root{--bg:#161616;--fg:#e8e8e8;--mute:#999;--line:#333;--accent:#6cb2ff}}
body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);font:16px/1.7 "Noto Sans JP",system-ui,sans-serif}
main{max-width:640px;margin:0 auto}h1{font-size:1.3rem;margin:0 0 8px}p,.mute{color:var(--mute);margin:4px 0}
label{display:block;margin:14px 0 4px;font-weight:700}
input,select{width:100%;box-sizing:border-box;padding:9px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
button{margin-top:18px;padding:10px 18px;border:0;border-radius:6px;background:var(--accent);color:#fff;font:inherit;font-weight:700;cursor:pointer}
button:disabled{opacity:.5}
#msg{font-weight:700}.ok{color:#1a7f37}.ng{color:#c62828}
</style></head><body><main>
<h1>放送の情報を入れる</h1>
<p class="mute">放送日から3日たつと、ここには出なくなります。Tver のリンクとゲスト名は、フィードの記事に後ろから足されます。</p>
<label for="key">鍵</label>
<input id="key" type="password" autocomplete="current-password">
<label for="item">項目（放送日の前後10日）</label>
<select id="item"><option value="">読み込み中…</option></select>
<p class="mute" id="until"></p>
<label for="tver">Tver のURL（任意）</label>
<input id="tver" type="url" placeholder="https://tver.jp/episodes/..." maxlength="200">
<label for="guest">ゲスト（任意）</label>
<input id="guest" maxlength="60">
<button id="save" type="button">保存</button>
<p id="msg" role="status"></p>
</main>
<script>
const $ = (id) => document.getElementById(id);
const DAY = 86400000;
const jst = (d) => new Date(d.getTime() + 9 * 3600000).toISOString().slice(0, 10);
const plus = (day, n) => { const d = new Date(day + "T00:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
let rows = [];
(async () => {
  try {
    const opts = await (await fetch("/data/options.json", { cache: "no-store" })).json();
    const today = jst(new Date());
    const lo = plus(today, -10), hi = plus(today, 10);
    for (const s of opts.sources || []) {
      const d = await (await fetch(s.file, { cache: "no-store" })).json();
      for (const it of d.items || []) {
        const air = String(it.air || "").slice(0, 10);
        if (it.guid.includes("-media-") && air && air >= lo && air <= hi) rows.push({ guid: it.guid, air, name: it.name || it.title, site: s.siteLabel });
      }
    }
    rows.sort((a, b) => a.air.localeCompare(b.air));
    const sel = $("item");
    sel.textContent = "";
    const first = document.createElement("option");
    first.value = ""; first.textContent = rows.length ? "選ぶ" : "（放送日の前後10日に項目がありません）";
    sel.appendChild(first);
    rows.forEach((r, i) => {
      const o = document.createElement("option");
      o.value = String(i); o.textContent = r.air + "  " + r.name + "（" + r.site + "）";
      sel.appendChild(o);
    });
  } catch (e) {
    $("msg").className = "ng"; $("msg").textContent = "項目が読めませんでした";
  }
})();
$("item").addEventListener("change", () => {
  const r = rows[Number($("item").value)];
  $("until").textContent = r ? "この項目は " + plus(r.air, ${DAYS_AFTER}) + " まで出ます" : "";
});
$("save").addEventListener("click", async () => {
  const r = rows[Number($("item").value)];
  const msg = $("msg");
  if (!r) { msg.className = "ng"; msg.textContent = "項目を選んでください"; return; }
  $("save").disabled = true; msg.className = ""; msg.textContent = "保存しています…";
  try {
    const res = await fetch("/import/save", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key: $("key").value, guid: r.guid, tver: $("tver").value.trim(), guest: $("guest").value.trim() }),
    });
    const j = await res.json().catch(() => ({}));
    if (res.ok) { msg.className = "ok"; msg.textContent = "保存しました。" + (j.note || "フィードに反映されるまで、少し待ってください"); }
    else { msg.className = "ng"; msg.textContent = j.error || ("失敗しました（" + res.status + "）"); }
  } catch (e) {
    msg.className = "ng"; msg.textContent = "通信できませんでした";
  } finally {
    $("save").disabled = false;
  }
});
</script>
</main></body></html>
`;

// Worker の入口から呼ぶ。対象外なら null を返す
export async function importRoutes(req, env, { now, dispatch }) {
  const url = new URL(req.url);
  if (req.method === "GET" && url.pathname === "/import") {
    return new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
  }
  if (req.method === "POST" && url.pathname === "/import/save") {
    return save(req, env, now, dispatch);
  }
  return null;
}

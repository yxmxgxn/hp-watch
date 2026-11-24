// 「ご連絡」のフォーム（このサイトについて、のページ）の受け口。POST /report
// 受け取った内容は、保存しない（KV なども持たない）。作者への通知に送るだけ。ログにも、内容は残さない。
// 通知先が未設定のときは、受け付けない（送れたように見せない）。

const UA = "hello-rss/report";
const MAX_BODY = 8 * 1024;
const LIMITS = { target: 300, contact: 200, message: 2000 };

const clean = (s, max) => String(s ?? "").replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, " ").trim().slice(0, max);
const json = (status, body) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" } });

export async function reportRoute(req, env) {
  const url = new URL(req.url);
  if (url.pathname !== "/report") return null;
  if (req.method !== "POST") return new Response("POST only", { status: 405, headers: { Allow: "POST" } });
  // 同じサイトのページからだけ（ほかのサイトのページから、勝手に送らせない）
  if (req.headers.get("Origin") !== url.origin) return json(403, { error: "このページから送ってください" });
  const text = await req.text();
  if (text.length > MAX_BODY) return json(413, { error: "長すぎます" });
  let body;
  try { body = JSON.parse(text); } catch { return json(400, { error: "内容が読めません" }); }
  if (!body || typeof body !== "object") return json(400, { error: "内容が読めません" });
  // 見えない欄（website）に書いてあれば、機械の入力。送れたように返して、通知しない
  if (clean(body.website, 50)) return json(200, { ok: true });
  const target = clean(body.target, LIMITS.target);
  if (!target) return json(400, { error: "不具合があるフィード（または、対象）を書いてください" });
  const contact = clean(body.contact, LIMITS.contact);
  const message = clean(body.message, LIMITS.message);
  if (!env.DISCORD_WEBHOOK_URL) return json(503, { error: "いま受け付けられません。時間をおいて、もう一度お試しください" });
  const content = `📩 ご連絡（hello-rss）\n対象: ${target}\n連絡先: ${contact || "（なし）"}\nひとこと: ${message || "（なし）"}`.slice(0, 1900);
  let res;
  try {
    res = await fetch(env.DISCORD_WEBHOOK_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "User-Agent": UA },
      // メンション（@everyone など）は、書いてあっても、動かさない
      body: JSON.stringify({ username: "hello-rss", content, allowed_mentions: { parse: [] } }),
    });
  } catch { res = null; }
  if (!res || !res.ok) return json(502, { error: "送れませんでした。時間をおいて、もう一度お試しください" });
  return json(200, { ok: true });
}

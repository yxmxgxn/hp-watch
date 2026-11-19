// 手元の確認: NODE_USE_ENV_PROXY=1 node watcher/test.mjs
// 公式サイトへの HEAD は本物、GitHub と Discord への呼び出しは偽物
import watcher, { run, builtRecently } from "./src/index.js";

const realFetch = globalThis.fetch;
let calls = [];
let githubStatus = 204;
globalThis.fetch = async (url, opts = {}) => {
  const u = String(url);
  if (u.startsWith("https://api.github.com/") || u.startsWith("https://discord.test/")) {
    calls.push({ url: u, opts });
    return new Response(githubStatus === 204 ? null : "boom", { status: u.includes("discord") ? 204 : githubStatus });
  }
  return realFetch(url, opts);
};
const env = { GH_REPO: "o/r", GH_TOKEN: "tok", DISCORD_WEBHOOK_URL: "https://discord.test/hook" };
const assert = (c, m) => { if (!c) { console.error("NG:", m); process.exit(1); } console.log("ok:", m); };
const gh = () => calls.filter((c) => c.url.includes("api.github.com")).map((c) => c.url.split("/workflows/")[1].split("/")[0]);

// 本物のサイトの Last-Modified を基準にする
const head = await realFetch("https://helloproject.com/schedule/", { method: "HEAD" });
const lm = new Date(head.headers.get("last-modified"));
console.log("公式の最後の更新:", lm.toISOString());
const at = (min) => new Date(lm.getTime() + min * 60000);

assert((await builtRecently("https://helloproject.com", at(5)))?.getTime() === lm.getTime(), "更新の5分後 → 直近の更新として検知");
assert((await builtRecently("https://helloproject.com", at(90))) === null, "更新の90分後 → 検知しない");

calls = []; let s = await run("*/15 * * * *", env, at(5));
assert(JSON.stringify(gh()) === '["rss.yml"]', "更新の直後（2サイトとも更新）でも rss.yml を1回だけ呼ぶ");
const c = calls[0];
assert(c.url === "https://api.github.com/repos/o/r/actions/workflows/rss.yml/dispatches", "呼び出し先のURL");
assert(c.opts.method === "POST" && c.opts.headers.Authorization === "Bearer tok" && JSON.parse(c.opts.body).ref === "main", "POST・トークン・ref=main");

calls = []; await run("*/15 * * * *", env, at(90));
assert(gh().length === 0, "更新から90分後は何も呼ばない");

calls = []; await run("0 9 * * *", env, at(90));
assert(JSON.stringify(gh()) === '["hpwatch.yml","profiles.yml"]', "18:00 JST は hpwatch.yml と profiles.yml を別々に呼ぶ");

calls = []; await run("0 20 * * *", env, at(90));
assert(JSON.stringify(gh()) === '["rss.yml"]', "05:00 JST は rss.yml");

calls = []; await run("0 3 * * *", env, at(5));
assert(calls.length === 0, "知らない時刻の cron は何もしない");

// 失敗: 401 はやり直さず、Discord に知らせる（毎時の最初の15分だけ）
githubStatus = 401; calls = [];
s = await run("0 9 * * *", env, new Date("2026-11-19T09:00:00Z"));
assert(s.failed.length === 2 && gh().length === 2, "401 はやり直さない（2作業×1回）");
assert(calls.filter((x) => x.url.includes("discord")).length === 1, "失敗を Discord に1回知らせる");
calls = []; await run("*/15 * * * *", env, at(5)); // 更新の検知で呼ぶ回。時刻（:30 など）に関係なく知らせる
assert(calls.filter((x) => x.url.includes("discord")).length === 1, "どの時刻の失敗でも Discord に知らせる");
calls = []; await run("0 9 * * *", { ...env, DISCORD_WEBHOOK_URL: undefined }, new Date("2026-11-19T09:00:00Z"));
assert(calls.filter((x) => x.url.includes("discord")).length === 0, "知らせ先が未設定なら何もしない");

// 500 は3回までやり直す
githubStatus = 500; calls = [];
s = await run("0 20 * * *", env, new Date("2026-11-19T20:30:00Z"));
assert(gh().length === 3 && s.failed.length === 1, "500 は3回までやり直す");

assert((await watcher.fetch()).status === 404, "HTTP は404");

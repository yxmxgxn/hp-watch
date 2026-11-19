// 手元の確認: NODE_USE_ENV_PROXY=1 node watcher/test.mjs   （本物の公式サイトにHEAD/GETする。KV は偽物）
import watcher, { poll } from "./src/index.js";

const store = new Map();
const env = {
  LOG_PATH: "secret",
  WATCH: { get: async (k) => store.get(k) ?? null, put: async (k, v) => void store.set(k, v) },
};
const assert = (c, m) => { if (!c) { console.error("NG:", m); process.exit(1); } console.log("ok:", m); };

let e = await poll(env, new Date());
assert(e.length === 2 && e.every((x) => x.first && x.version && x.versionTime), "初回: 2サイトとも版と更新時刻が取れる");
console.log(e.map((x) => `${x.site} ${x.version} ${x.versionTime} news=${x.news?.bytes} sched=${x.schedule?.bytes}`).join("\n"));
assert(/^\d{10}-/.test(e[0].version), "版の名前が 年月日時分-... の形");

e = await poll(env, new Date());
assert(e.length === 0, "2回目: 更新が無ければ記録を足さない");

// 更新があったことにする（前回の ETag と、お知らせのサイズを書き換える）
const st = JSON.parse(store.get("state:hp"));
st.pageEtag = '"old"'; st.news = { bytes: st.news.bytes - 2048, etag: '"old"' };
store.set("state:hp", JSON.stringify(st));
e = await poll(env, new Date());
assert(e.length === 1 && e[0].site === "hp" && e[0].news.changed === true && e[0].news.delta === 2048, "更新あり: お知らせが +2KB 増えたと記録する");
assert(e[0].schedule.changed === false, "スケジュールは変化なしと記録する");

const text = await (await watcher.fetch(new Request("https://x/secret?text"), env)).text();
console.log(text);
assert(text.includes("お知らせ +2.0KB"), "?text で読める");
assert((await watcher.fetch(new Request("https://x/other"), env)).status === 404, "別のパスは404");

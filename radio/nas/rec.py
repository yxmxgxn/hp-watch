#!/usr/bin/env python3
"""
radiko のタイムフリーから、番組リストの回を取りこぼしなく NAS と BOX に保存する。

- 放送時刻に合わせて録らない。数時間おきに「過去1週間で、まだ保存していない回」を探して落とす
  （NAS の再起動やコンテナの停止があっても、1週間以内に動けば取り戻せる）
- ファイル名と回数の付け方は radio_rename.ps1 と同じ: 第{回}回 {YYYYMMDD}_{番組名}.m4a
  回数は保存先フォルダの最大の回 +1（1本も無ければ first_no）。同じ日付の回があれば保存済みとみなす
- 番組リストはリポジトリの radio/programs.json（スマホの GitHub アプリで直す）。取れないときは前回の内容で動く
- 保存した回と異常は Discord に直接送る。毎回 healthchecks.io に生存を知らせ、途絶えたらそちらが知らせる

録音そのものは rec_radiko_ts.sh（https://github.com/uru2/rec_radiko_ts）に任せる。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))
GITHUB_REPO = os.environ.get("GITHUB_REPO", "").strip()     # 例 yxmxgxn/hp-watch
GITHUB_PATH = os.environ.get("GITHUB_PATH", "radio/programs.json").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()   # このリポジトリの Contents を読めるだけのトークン
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "").strip()  # healthchecks.io の Ping URL
NAS_ROOT = Path(os.environ.get("NAS_ROOT", "/nas"))
BOX_REMOTE = os.environ.get("BOX_REMOTE", "").strip()   # rclone のリモート名（例 "box:"）。空なら BOX へは送らない
RADIKO_MAIL = os.environ.get("RADIKO_MAIL", "").strip()  # プレミアム会員なら。空でも住んでいる地域の局は録れる
RADIKO_PASS = os.environ.get("RADIKO_PASS", "")
DATA = Path(os.environ.get("DATA_DIR", "/data"))
INTERVAL_HOURS = float(os.environ.get("INTERVAL_HOURS", "6"))

TIMEFREE_DAYS = 7                    # タイムフリーで聴ける期間
READY_AFTER = timedelta(minutes=30)  # 放送終了からタイムフリーに出るまでの余裕
ALERT_AFTER = timedelta(hours=48)    # 一時的な失敗で騒がないよう、放送からこれだけ経っても取れなければ知らせる
REALERT = timedelta(hours=24)        # 同じことを知らせ直す間隔
UA = "Mozilla/5.0 (radio-recorder)"

STATE_PATH = DATA / "state.json"
STATE_ALERTS = {}  # 番組ごとでない知らせの、最後に知らせた時刻（state.json の alerts）
WORK = DATA / "work"


def log(msg):
    print(f"{datetime.now(JST):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def http(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def notify(report):
    """保存した回か、知らせることがあるときだけ Discord へ"""
    lines = [f"⚠️ {a}" for a in report["alerts"]] + [f"✅ {x}" for x in report["saved"]]
    if not lines or not DISCORD_WEBHOOK:
        return
    body = {"embeds": [{
        "title": "ラジオ録音: 要確認" if report["alerts"] else "ラジオ録音",
        "description": "\n".join(lines)[:4000],
        "color": 0xD9534F if report["alerts"] else 0x5CB85C,
    }]}
    http(DISCORD_WEBHOOK, json.dumps(body, ensure_ascii=False).encode(), {"Content-Type": "application/json"})


def ts(s):
    """radiko の 20261019010000 を datetime に"""
    return datetime.strptime(s[:14], "%Y%m%d%H%M%S").replace(tzinfo=JST)


def norm(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def safe_name(s):
    """Windows/SMB で使えない文字を除く（Worker 側と同じ）"""
    return re.sub(r'[\\/:*?"<>|]', "", s).strip()


def load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(state):
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def load_programs(alerts):
    """GitHub の programs.json を読む。読めない・壊れているときは前回読めた内容で動く"""
    cache = DATA / "programs.json"
    try:
        raw = http(f"https://api.github.com/repos/{GITHUB_REPO}/contents/{GITHUB_PATH}",
                   headers={"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github.raw"})
        programs = [check_program(p) for p in json.loads(raw)["programs"]]
        cache.write_bytes(raw)
    except Exception as e:
        if not cache.exists():
            raise
        log(f"programs.json を読めず、前回の内容で実行: {e}")
        if alert_due(STATE_ALERTS.setdefault("programs", {}), datetime.now(JST)):
            alerts.append(f"programs.json を読めず前回の内容で実行しました（{e}）")
        programs = [check_program(p) for p in json.loads(cache.read_bytes())["programs"]]
    else:
        STATE_ALERTS.pop("programs", None)
    return [p for p in programs if p["enabled"]]


def check_program(p):
    """スマホで書き換えるので、書き間違いはここで弾く・揃える"""
    name = safe_name(str(p.get("name") or ""))
    station = str(p.get("station") or "").strip().upper()
    if not name or not re.fullmatch(r"[A-Z0-9-]+", station) or not p.get("dir"):
        raise ValueError(f"name / station / dir のどれかがおかしい: {p}")
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})", str(p.get("start") or "").strip())
    first_no = p.get("first_no")
    return {
        "name": name, "station": station, "match": str(p.get("match") or ""),
        "start": f"{int(m.group(1)) % 24:02d}:{m.group(2)}" if m else "",  # 25:30 → 01:30
        "dir": str(p["dir"]).replace("\\", "/").strip("/"), "box": str(p.get("box") or "").replace("\\", "/").strip("/"),
        "first_no": int(first_no) if first_no not in (None, "") else None,
        "enabled": p.get("enabled", True) is not False,
    }


# ---------------------------------------------------------------- 番組表

class Radiko:
    """1回の実行の間だけ番組表をキャッシュする（同じ地域の番組は1回で済む）"""

    def __init__(self):
        self._areas = None
        self._days = {}

    def area_of(self, station):
        if self._areas is None:
            root = ET.fromstring(http("https://radiko.jp/v3/station/region/full.xml"))
            self._areas = {s.findtext("id"): s.findtext("area_id") for s in root.iter("station")}
        if station not in self._areas:
            raise ValueError(f"radiko に局ID {station} がありません")
        return self._areas[station]

    def progs(self, station, day):
        area = self.area_of(station)
        key = (area, day)
        if key not in self._days:
            self._days[key] = ET.fromstring(http(f"https://api.radiko.jp/program/v3/date/{day}/area/{area}.xml"))
        for st in self._days[key].iter("station"):
            if st.get("id") == station:
                for p in st.iter("prog"):
                    yield {"ft": p.get("ft"), "to": p.get("to"), "title": p.findtext("title") or ""}


RERUN = re.compile(r"\(再\)|【再】|\[再\]|再放送")


def find_episodes(rk, prog, now):
    """過去1週間の該当回を、古い順に。タイムフリーに出ているものだけ。再放送は除く"""
    want = norm(prog.get("match") or prog["name"])
    start = (prog.get("start") or "").replace(":", "")
    found = {}
    for i in range(TIMEFREE_DAYS + 1):
        day = (now - timedelta(days=i, hours=5)).strftime("%Y%m%d")  # radiko の1日は 5:00 から
        for p in rk.progs(prog["station"], day):
            title = norm(p["title"])
            if want in title and not RERUN.search(title) and (not start or p["ft"][8:12] == start):
                found[p["ft"]] = p
    oldest = now - timedelta(days=TIMEFREE_DAYS) + timedelta(hours=1)
    return [p for ft, p in sorted(found.items())
            if ts(ft) > oldest and ts(p["to"]) + READY_AFTER <= now]


# ---------------------------------------------------------------- 保存

def nas_dir(prog):
    rel = Path(prog["dir"])
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"保存先が共有フォルダの外です: {prog['dir']}")
    return NAS_ROOT / rel


def scan(folder, name):
    """保存先の最大の回・保存済みの日付・最新の日付（radio_rename.ps1 の Scan と同じ規則）"""
    pat = re.compile(r"^第(\d+)回[ _](\d{8})_" + re.escape(name) + r"\.m4a$")
    max_no, dates = 0, set()
    if folder.is_dir():
        for f in folder.iterdir():
            m = pat.match(f.name)
            if m:
                max_no = max(max_no, int(m.group(1)))
                dates.add(m.group(2))
    return max_no, dates, max(dates, default="")


def record(ep, station):
    WORK.mkdir(parents=True, exist_ok=True)
    out = WORK / f"{station}_{ep['ft']}.m4a"
    out.unlink(missing_ok=True)
    cmd = ["rec_radiko_ts.sh", "-s", station, "-f", ep["ft"][:14], "-t", ep["to"][:14], "-o", str(out)]
    if RADIKO_MAIL:
        cmd += ["-m", RADIKO_MAIL, "-p", RADIKO_PASS]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=4 * 3600)
    if r.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        err = (r.stderr or r.stdout).strip().splitlines()
        raise RuntimeError("録音に失敗: " + (err[-1] if err else f"終了コード {r.returncode}"))

    # 途中で切れたファイルを保存済みにしないよう、長さを確かめる
    want = (ts(ep["to"]) - ts(ep["ft"])).total_seconds()
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True)
    try:
        got = float(p.stdout.strip())
    except ValueError:
        got = 0
    if got < want * 0.95:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"録音が短い（{got / 60:.0f}分 / 本来{want / 60:.0f}分）")
    return out


def copy_atomic(src, dest):
    """途中で止まっても中途半端なファイルが「保存済み」に見えないよう、別名で書いてから名前を変える"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)
    os.replace(part, dest)


def run_program(rk, prog, state, now, report):
    name = safe_name(prog["name"])
    folder = nas_dir(prog)
    max_no, dates, latest = scan(folder, name)
    fails = state.setdefault("fails", {})

    for ep in find_episodes(rk, prog, now):
        date = ep["ft"][:8]
        key = f"{prog['station']}:{ep['ft']}"
        if date in dates:
            fails.pop(key, None)
            continue
        if latest and date < latest:
            continue  # 保存先の最新回より古い = 自動にする前の回。番号が逆転するので触らない

        no = max_no + 1 if max_no else prog.get("first_no")
        if not no:
            report["alerts"].append(f"{name}: 保存先に番号付きのファイルが無く、最初の回数も未設定です")
            return
        fname = f"第{no}回 {date}_{name}.m4a"
        when = ts(ep["ft"]).strftime("%m/%d %H:%M")
        log(f"録音: {fname}（{ep['title']} {ep['ft']}〜{ep['to']}）")
        try:
            tmp = record(ep, prog["station"])
            dest = folder / fname
            if dest.exists():
                raise RuntimeError(f"同じ名前のファイルが既にあります: {dest}")
            copy_atomic(tmp, dest)
            tmp.unlink(missing_ok=True)
        except Exception as e:
            f = fails.setdefault(key, {"label": f"{name} {when}放送", "ft": ep["ft"]})
            f["error"] = str(e)[:300]
            log(f"  失敗: {e}")
            # この回を飛ばして次の回を保存すると回数がずれるので、取れるまで先へ進まない
            return

        fails.pop(key, None)
        max_no, latest = no, date
        dates.add(date)
        report["saved"].append(f"{fname}")
        log(f"  NAS に保存: {dest}")
        if BOX_REMOTE and prog.get("box"):
            state.setdefault("box_pending", []).append({
                "local": str(dest), "remote": f"{BOX_REMOTE}{prog['box'].strip('/')}/{fname}",
                "since": now.isoformat(),
            })


def upload_box(state, now, report):
    """NAS に置いたファイルを BOX へ。失敗したものは次の実行でもう一度"""
    left = []
    for job in state.get("box_pending", []):
        if not Path(job["local"]).exists():
            continue  # NAS 側で消した・名前を変えた
        r = subprocess.run(["rclone", "copyto", job["local"], job["remote"]], capture_output=True, text=True,
                           timeout=3600)
        if r.returncode == 0:
            log(f"BOX に保存: {job['remote']}")
            continue
        err = (r.stderr.strip().splitlines() or ["?"])[-1]
        log(f"BOX への保存に失敗: {job['remote']} / {err}")
        job["error"] = err[:300]
        if now - datetime.fromisoformat(job["since"]) > timedelta(hours=24) and alert_due(job, now):
            report["alerts"].append(f"BOX へ送れていません: {Path(job['local']).name}（{job['error']}）")
        left.append(job)
    state["box_pending"] = left


def alert_due(item, now):
    last = item.get("alerted")
    if last and now - datetime.fromisoformat(last) < REALERT:
        return False
    item["alerted"] = now.isoformat()
    return True


def review_fails(state, now, report):
    fails = state.get("fails", {})
    for key, f in list(fails.items()):
        aired = ts(f["ft"])
        if aired < now - timedelta(days=TIMEFREE_DAYS):
            report["alerts"].append(f"取り逃しました: {f['label']}（タイムフリーの期限切れ。最後のエラー: {f['error']}）"
                                    " 次の回はこの回の番号で保存されます")
            del fails[key]
        elif now - aired > ALERT_AFTER and alert_due(f, now):
            left = aired + timedelta(days=TIMEFREE_DAYS) - now
            report["alerts"].append(f"まだ取れていません: {f['label']}（{f['error']}）"
                                    f" 期限まであと約{left.days}日{left.seconds // 3600}時間")


def run_once():
    now = datetime.now(JST)
    state = load_state()
    STATE_ALERTS.clear()
    STATE_ALERTS.update(state.setdefault("alerts", {}))
    report = {"saved": [], "alerts": []}
    try:
        programs = load_programs(report["alerts"])
        rk = Radiko()
        errors = state.setdefault("errors", {})
        for prog in programs:
            try:
                run_program(rk, prog, state, now, report)
                errors.pop(prog["name"], None)
            except Exception as e:
                # 番組表が取れない・局IDの間違いなど。録音の失敗は run_program の中で数える
                log(f"{prog['name']}: {e}")
                err = errors.setdefault(prog["name"], {"since": now.isoformat()})
                if alert_due(err, now):
                    report["alerts"].append(f"{prog['name']}: {e}")
        upload_box(state, now, report)
        review_fails(state, now, report)
    except Exception as e:
        log(traceback.format_exc())
        report["alerts"].append(f"実行に失敗しました: {e}")
    finally:
        state["alerts"] = dict(STATE_ALERTS)
        save_state(state)

    try:
        notify(report)
    except Exception as e:
        log(f"Discord への通知に失敗: {e}")
    if HEALTHCHECK_URL:
        try:
            # 生きていることだけ知らせる（中身の異常は Discord で知らせ済み）
            http(HEALTHCHECK_URL, timeout=30)
        except Exception as e:
            log(f"healthchecks.io への通知に失敗: {e}")
    log(f"完了: 保存 {len(report['saved'])} 本 / 要確認 {len(report['alerts'])} 件")


def main():
    if not GITHUB_REPO or not GITHUB_TOKEN:
        sys.exit("GITHUB_REPO と GITHUB_TOKEN を設定してください")
    DATA.mkdir(parents=True, exist_ok=True)
    while True:
        run_once()
        if "--once" in sys.argv:
            break
        time.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    main()

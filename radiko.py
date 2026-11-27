"""radiko の局と番組表（RSS の MEDIA で、ラジオにタイムフリーのリンクを付けるため）

- 局: 公式スケジュールのタイトル「ラジオ日本/ラジオ関西「番組名」」の局名から radiko の局IDを引く
- 番組表: 局ごとの週間番組表（前後1週間ほど）で、その時刻の番組の開始時刻とタイムフリー可否を見る
- 地域: 都道府県ごとの局一覧（47本）から、その局を無料で聴ける範囲を出す
  番組表の範囲外（1週間より先）は、公式の時刻でそのままリンクを作る（公式の時刻は番組の開始と一致していた）
"""
import datetime as dt
import json
import os
import re
import sys
import time
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (hp-watch; personal schedule rss)"
# 番組のページ（/r_seasons/…）の URL は、スマホ向けのエピソードのページ（/mobile/events/番組表のID）の中にある。PC 向けの UA だと入っていない
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
TIMEFREE_MARGIN = dt.timedelta(minutes=30)   # 番組の終了から、この時間がたてば、確実にタイムフリーで聴ける、とみなす（今日の予定のページが、それまで、タイムフリーを隠す）
SEASON_RETRY_DAYS = 7     # 番組のページが無かった番組を、探し直すまでの日数
SEASON_MAX_FETCH = 200    # 1回の作り直しで、エピソードのページを取りに行く数の上限（新しい番組のぶんだけ取るので、ふだんは数件）
PART = re.compile(r"\s*[(（]\d+[)）]\s*$")   # 長時間の番組の「題名 (1)」「題名 (2)」

PREFS = ("北海道 青森 岩手 宮城 秋田 山形 福島 茨城 栃木 群馬 埼玉 千葉 東京 神奈川 新潟 富山 石川 福井 山梨 長野 岐阜 "
         "静岡 愛知 三重 滋賀 京都 大阪 兵庫 奈良 和歌山 鳥取 島根 岡山 広島 山口 徳島 香川 愛媛 高知 福岡 佐賀 長崎 "
         "熊本 大分 宮崎 鹿児島 沖縄").split()

# 無料で聴ける範囲をまとめて書くときの名前（都道府県の集合がちょうど一致したときだけ使う）
REGIONS = {
    "関東": {"茨城", "栃木", "群馬", "埼玉", "千葉", "東京", "神奈川"},
    "関西": {"滋賀", "京都", "大阪", "兵庫", "奈良", "和歌山"},
    "東海": {"岐阜", "愛知", "三重"},
}

# 公式の局名と radiko の局名が違うもの（文字の幅・空白をそろえても合わないものだけ）
ALIAS = {
    "ぎふチャン": "GBS",
    "九州朝日放送": "KBC",
    "東海ラジオ": "TOKAIRADIO",
    "熊本放送": "RKK",
    "RCC中国放送": "RCC",
    "ABC朝日放送": "ABC",
    "RABラジオ": "RAB",
    "YBCラジオ": "YBC",
    "FM岩手": "FMI",
    "エフエム石川": "HELLOFIVE",
    "ラジオNIKKEI": "RN1",  # 第1と第2がある。ふつうは第1
    "BSS山陰放送": "BSS",
    "FM山形": "RFM",
    # 公式は「○○放送」、radiko は「MROラジオ」「IBCラジオ」のように、局の略称で出している局
    "北陸放送": "MRO",
    "南日本放送": "MBC",
    "岩手放送": "IBC",
    "静岡放送": "SBS",
    "山陰放送": "BSS",
    "新潟放送": "BSN",
    "BSN新潟放送": "BSN",
    "岐阜放送": "GBS",
    "HBC北海道放送": "HBC",
    "NHK-FM": "JOAK-FM",  # NHK は地域ごとに別の局。FM は全国同じ番組なので、東京
    # 公式の局名の書き方が、同じ局でも、いくつかある（「FM石川」と「エフエム石川」など）・radiko の名前と違う局。2026年11月の公式の予定から
    "FM石川": "HELLOFIVE",
    "FM大分": "FM_OITA",
    "FM香川": "FMKAGAWA",
    "FM新潟": "FMNIIGATA",
    "FM-NIIGATA": "FMNIIGATA",
    "FM佐賀": "FMS",
    "FM山陰": "FM-SANIN",
    "FM山口": "FMY",
    "FM青森": "AFB",
    "FM宮崎": "JOYFM",
    "FM熊本": "FMK",
    "FM福岡": "FMFUKUOKA",
    "エフエム福岡": "FMFUKUOKA",
    "エフエム岐阜": "FMGIFU",
    "ラブエフエム": "LOVEFM",
    "AIR-G’": "AIR-G",
    "IBC岩手放送": "IBC",
    "秋田放送": "ABS",
    "信越放送": "SBC",
    "大分放送": "OBS",
    "北日本放送": "KNB",
    "MRTラジオ": "MRT",
    "長崎放送": "NBC",
    "RKK 熊本放送": "RKK",
    "琉球放送": "RBC",
    "RBC琉球放送": "RBC",
    "山陽放送": "RSK",
    "KBS京都放送": "KBS",
    "福井放送": "FBC",
    "tbc東北放送": "TBC",
    "NHK広島": "JOFK",  # NHK AM(広島)
}


def _get(url, ua=UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")  # 公式の局名に混ざる、見えない文字（空白としては扱われない）


def norm(s):
    return re.sub(r"\s", "", ZERO_WIDTH.sub("", unicodedata.normalize("NFKC", s or ""))).upper()


def ft_of(date, start):
    """公式の日付と "25:10" のような時刻から radiko の 20261121011000 を作る"""
    h, m = map(int, start.split(":"))
    t = dt.datetime.fromisoformat(date) + dt.timedelta(hours=h, minutes=m)
    return t.strftime("%Y%m%d%H%M%S")


class Radiko:
    def __init__(self, season_cache=None):
        self._season_path = season_cache   # 番組のページの記憶（state/radiko_seasons.json）。無ければ、記憶しない
        self._seasons = {}                 # 「局|番組名」→ {"id": 番組のページの番号（無ければ ""）, "at": 調べた日}
        self._seasons_dirty = False
        self._season_fetches = 0
        if season_cache and os.path.exists(season_cache):
            try:
                with open(season_cache, encoding="utf-8") as f:
                    self._seasons = json.load(f)
            except Exception as e:
                print(f"warn: radiko の番組のページの記憶が読めない: {e}", file=sys.stderr)
        self._stations = None  # id -> {"name"}
        self._by_name = None
        self._weekly = {}
        self._free = None  # id -> [無料で聴ける都道府県]

    def _load_stations(self):
        if self._stations is not None:
            return
        self._stations, self._by_name = {}, {}
        try:
            root = ET.fromstring(_get("https://radiko.jp/v3/station/region/full.xml"))
        except Exception as e:
            print(f"warn: radiko の局一覧: {e}", file=sys.stderr)
            return
        for s in root.iter("station"):
            sid = s.findtext("id")
            # 表示用。ＨＢＣラジオ → HBCラジオ、全角の空白 → 半角
            self._stations[sid] = {"name": unicodedata.normalize("NFKC", s.findtext("name") or sid)}
            for n in (s.findtext("name"), s.findtext("ascii_name")):
                if n:
                    self._by_name[norm(n)] = sid

    def station_ids(self, title):
        """「ラジオ日本/ラジオ関西「番組名」」→ ["JORF", "CRK"]。わからない局は飛ばす"""
        self._load_stations()
        head = title.split("「", 1)[0] if "「" in title else ""
        ids = []
        for part in re.split(r"[/／]", head):
            part = ZERO_WIDTH.sub("", part).strip()
            if not part:
                continue
            sid = ALIAS.get(part) or self._by_name.get(norm(part))
            if not sid:  # 「エフエム石川」と「HELLO FIVE エフエム石川」のような包含
                hits = {v for k, v in self._by_name.items() if norm(part) in k or k in norm(part)}
                sid = hits.pop() if len(hits) == 1 else None
            if sid:
                ids.append(sid)
        return ids

    def free_area(self, sid):
        """無料で聴ける範囲。radiko の都道府県ごとの局一覧（47本）から作る。取れなければ空"""
        if self._free is None:
            self._free = {}
            try:
                for i, pref in enumerate(PREFS, 1):
                    root = ET.fromstring(_get(f"https://radiko.jp/v3/station/list/JP{i}.xml"))
                    for s in root.iter("station"):
                        self._free.setdefault(s.findtext("id"), []).append(pref)
            except Exception as e:
                print(f"warn: radiko の地域ごとの局一覧: {e}", file=sys.stderr)
                self._free = {}
        prefs = self._free.get(sid) or []
        if not prefs:
            return ""
        if len(prefs) == len(PREFS):
            return "全国"
        for name, members in REGIONS.items():
            if set(prefs) == members:
                return name
        return "・".join(prefs)

    def name(self, sid):
        self._load_stations()
        return (self._stations.get(sid) or {}).get("name", sid)

    def _progs(self, sid):
        if sid not in self._weekly:
            try:
                root = ET.fromstring(_get(f"https://api.radiko.jp/program/v3/weekly/{sid}.xml"))
                self._weekly[sid] = [{
                    "ft": p.get("ft"), "to": p.get("to"), "title": p.findtext("title") or "", "id": p.get("id"),
                    "ts_ng": p.findtext("ts_in_ng") == "1",
                } for p in root.iter("prog")]
                self._weekly[sid].sort(key=lambda p: p["ft"])
            except Exception as e:
                print(f"warn: radiko の番組表 {sid}: {e}", file=sys.stderr)
                self._weekly[sid] = []
        return self._weekly[sid]

    def season_url(self, sid, p, today=None):
        """番組のページ（最新回を再生・過去の放送回の一覧。スマホでも PC でも、そのまま開く）の URL。無ければ None。
        p は lookup の結果。番組表の放送ごとの ID から、スマホ向けのエピソードのページを開き、中の /r_seasons/番号 を読む。
        「局＋番組名（(1)(2) の番号を除く）」ごとに記憶して、新しい番組のぶんだけ取りに行く。無かった番組は、SEASON_RETRY_DAYS 日あとに探し直す。
        取れなかった（通信の失敗）ときは、記憶せず、None（次の作り直しで、またやる）"""
        pid, title = (p or {}).get("id"), (p or {}).get("title")
        if not pid or not title:
            return None
        key = f"{sid}|{norm(PART.sub('', title))}"
        today = today or dt.date.today()
        e = self._seasons.get(key)
        if e:
            if e.get("id"):
                return season_page(e["id"])
            try:
                if (today - dt.date.fromisoformat(e.get("at") or "")).days < SEASON_RETRY_DAYS:
                    return None
            except ValueError:
                pass
        if self._season_fetches >= SEASON_MAX_FETCH:
            return None
        self._season_fetches += 1
        try:
            html = _get(f"https://radiko.jp/mobile/events/{pid}", MOBILE_UA).decode("utf-8", "replace")
        except Exception as ex:
            print(f"warn: radiko のエピソードのページ {pid}: {ex}", file=sys.stderr)
            return None
        time.sleep(0.1)
        m = re.search(r"/r_seasons/(\d+)", html)
        self._seasons[key] = {"id": m.group(1) if m else "", "at": today.isoformat()}
        self._seasons_dirty = True
        return season_page(m.group(1)) if m else None

    def save_seasons(self):
        """番組のページの記憶を書く（変わったときだけ）"""
        if not self._season_path or not self._seasons_dirty:
            return False
        os.makedirs(os.path.dirname(self._season_path) or ".", exist_ok=True)
        with open(self._season_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(self._seasons, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        self._seasons_dirty = False
        return True

    def lookup(self, sid, ft):
        """その時刻の番組を番組表で探す。
        戻り値: {"ft", "title", "ts_ng", "corner"}（corner: 公式の時刻が番組の途中）
               None（番組表の範囲内なのに番組が無い＝リンクを付けない）
               {"ft": ft, "unchecked": True}（番組表の範囲外・取れない＝公式の時刻のまま）"""
        progs = self._progs(sid)
        if not progs or not (progs[0]["ft"] <= ft < progs[-1]["to"]):
            return {"ft": ft, "unchecked": True}
        for p in progs:
            if p["ft"] <= ft < p["to"]:
                return {"ft": p["ft"], "to": p["to"], "title": p["title"], "id": p["id"], "ts_ng": p["ts_ng"], "corner": p["ft"] != ft}
        return None


def ready_at(p, ft):
    """タイムフリーで確実に聴けるようになる時刻（日本時間の ISO）。番組の終了 ＋ TIMEFREE_MARGIN。
    番組表の外で終了が分からないときは、開始の1時間後を終了とみなす"""
    end = (p or {}).get("to")
    t = dt.datetime.strptime(end or ft, "%Y%m%d%H%M%S") + (dt.timedelta() if end else dt.timedelta(hours=1)) + TIMEFREE_MARGIN
    return t.strftime("%Y-%m-%dT%H:%M:%S+09:00")


def season_page(season_id):
    return f"https://radiko.jp/r_seasons/{season_id}"


def timefree_url(sid, ft):
    """タイムフリーのリンク。radiko の「シェア」用の URL（PC のブラウザでは再生ページ、スマホでは、その番組のページが開く）。
    昔の形（#!/ts/局/日時）は、スマホだと、スマホ版のトップに飛ばされた"""
    return f"https://radiko.jp/share/?sid={sid}&t={ft}"



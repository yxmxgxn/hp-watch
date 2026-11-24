"""tver.py の確かめ（ブラウザは使わない）。  python3 test_tver.py"""
import datetime as dt
import json
import os
import tempfile

import tver

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise SystemExit(f"NG: {msg}")
    ok += 1
    print("ok:", msg)


# 番組名の取り出し
check(tver.parse_program("HBCテレビ「キタコイ」") == ("HBCテレビ", "キタコイ"), "局名「番組名」を分ける")
check(tver.parse_program("仙台放送 「みやぎお仕事バラエティ スゴ！ジョブ！」") == ("仙台放送", "みやぎお仕事バラエティ スゴ！ジョブ！"), "局名と「の間の空白があっても分ける")
check(tver.parse_program("ただの題名") is None, "「」が無ければ None")

# 検索結果の1行（本物の結果の形）
r = tver.parse_row("/episodes/epjtipyqhn", "二畳半レコード\n二畳半15周年「謝音祭」SP③／GENIC 増子敦貴コメント（#771）\n福島中央テレビ 11月21日(土)放送分\n11月29日(日)1:10 終了予定")
check(r["title"] == "二畳半レコード" and r["station"] == "福島中央テレビ" and (r["month"], r["day"]) == (11, 21), "結果の行: 番組名・放送局・放送日")
n = tver.parse_row("/episodes/x", "長崎国際テレビ\n寒い季節を快適に\n長崎国際テレビ 11月10日(火) 21:10\n配信終了まで1週間以上")
check(n["month"] is None, "「放送分」の無い行（ニュースなど）は、放送日なし")

air = dt.date(2026, 11, 21)
rows = [
    tver.parse_row("/episodes/a", "二畳半レコード\nSP\n福島中央テレビ 11月21日(土)放送分\n終了予定"),
    tver.parse_row("/episodes/b", "二畳半レコード\n前の週\n福島中央テレビ 11月14日(土)放送分\n終了予定"),
    tver.parse_row("/episodes/c", "別の番組\nx\nテレビ朝日 11月21日(土)放送分\n終了予定"),
]
check(tver.pick(rows, "二畳半レコード", air) == "/episodes/a", "番組名と放送日が合うものを選ぶ")
check(tver.pick(rows, "二畳半レコード", dt.date(2026, 11, 22)) == "/episodes/a", "放送日が1日違いでも選ぶ")
check(tver.pick(rows, "二畳半レコード", dt.date(2026, 11, 25)) is None, "放送日が2日以上違えば選ばない（前の週は選ばない）")
check(tver.pick(rows, "TANTO MUSIK", air) is None, "番組名が合わない結果は選ばない")
check(tver.pick([tver.parse_row("/episodes/z", "キタコイ\nx\nHBC 11月21日(土)放送分\n")], "キタコイ", air) == "/episodes/z", "局名の書き方が違っても、番組名と日で選ぶ")
# 年をまたぐ
check(tver.nearest(dt.date(2027, 1, 1), 12, 31) == dt.date(2026, 12, 31), "年をまたぐ放送日（1/1 に 12/31）")
check(tver.pick([tver.parse_row("/episodes/y", "年末番組\nx\nA局 12月31日(木)放送分\n")], "年末番組", dt.date(2027, 1, 1)) == "/episodes/y", "年をまたいでも選べる")

# 毎週の番組: 地方局の遅れ放送（放送日の8日前までの、いちばん新しいエピソード）
rowsw = [tver.parse_row("/episodes/e1", "Hello! Music Lab。\nx\nテレ東 11月16日(月)放送分\n"),
         tver.parse_row("/episodes/e0", "Hello! Music Lab。\nx\nテレ東 11月9日(月)放送分\n")]
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 18)) is None, "単発は、2日以上ずれたら選ばない（水曜の遅れ放送には、選ばない）")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 18), back=8) == "/episodes/e1", "毎週の番組は、水曜の遅れ放送に、月曜のエピソードを選ぶ")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 16), back=8) == "/episodes/e1", "毎週の番組でも、同じ日のエピソードがあれば、それを選ぶ")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 25), back=8) is None, "毎週の番組でも、9日以上前のエピソードは選ばない")

# 探す項目
with tempfile.TemporaryDirectory() as d:
    sd = os.path.join(d, "state")
    os.makedirs(sd)
    def item(guid, sort, name, cat="TV"):
        return {"guid": guid, "sort": sort, "name": name, "title": name, "cats": [cat], "categories": [cat]}
    json.dump({"items": [
        item("g-today", "2026-11-23 24:59", "MBS「西乃風ブラン堂」"),
        item("g-old", "2026-11-18 24:00", "MBS「古い番組」"),            # 4日前: 期限外
        item("g-future", "2026-11-28 16:00", "日本テレビ「先の番組」"),  # 未来: まだ
        item("g-radio", "2026-11-23 20:00", "TBSラジオ「ラジオ番組」", "RADIO"),
        item("g-known", "2026-11-22 21:00", "日本テレビ「入っている番組」"),
        item("g-noname", "2026-11-22 21:00", "「」だけ無い題名"),
        item("g-edge", "2026-11-20 25:00", "HBCテレビ「ちょうど3日前」"),
    ]}, open(os.path.join(sd, "rss_media_hp.json"), "w", encoding="utf-8"), ensure_ascii=False)
    today = dt.date(2026, 11, 23)
    todo = tver.items_to_check(sd, today, {"g-known"})
    check(sorted(i["guid"] for i in todo) == ["g-edge", "g-today"], "探す項目: テレビ・3日前から今日・まだ無いものだけ")

    # 自動の分の書き方
    old = {"g-1": {"guid": "g-1", "air": "2026-11-18", "until": "2026-11-21", "links": [{"label": "TVer", "url": "https://tver.jp/episodes/a"}]},
           "g-2": {"guid": "g-2", "air": "2026-11-22", "until": "2026-11-25", "links": [{"label": "TVer", "url": "https://tver.jp/episodes/b"}]}}
    new = {"g-3": {"guid": "g-3", "air": "2026-11-23", "until": "2026-11-26", "links": []}}
    merged = tver.merge_auto(old, new, today)
    check([e["guid"] for e in merged] == ["g-2", "g-3"], "自動の分: 期限の切れたものは外し、新しいものを足す（放送日順）")

    # 期限内だけ読む・リンクの有無
    p = os.path.join(d, "auto.json")
    json.dump({"items": list(old.values())}, open(p, "w", encoding="utf-8"))
    ent = tver.load_entries(p, today)
    check(list(ent) == ["g-2"] and tver.has_link(ent["g-2"]), "ファイルを読む: 期限内だけ・リンクあり")
    check(tver.load_entries(os.path.join(d, "none.json"), today) == {}, "ファイルが無ければ空")

# 番組の記録（manual/programs.json）: TVer の検索のページを足す。ほかの欄は、そのまま
with tempfile.TemporaryDirectory() as d2:
    pp = os.path.join(d2, "programs.json")
    key = tver.rss.program_key("TV", "Hello! Music Lab。")
    json.dump({"programs": {key: {"name": "Hello! Music Lab。", "cat": "TV", "youtube": "https://www.youtube.com/@x"}}}, open(pp, "w", encoding="utf-8"))
    check(tver.update_programs(pp, {"Hello! Music Lab。", "西乃風ブラン堂"}) is True, "番組の記録: TVer の検索のページを足す")
    data = json.load(open(pp, encoding="utf-8"))["programs"]
    check(data[key]["tver"].startswith("https://tver.jp/search/") and data[key]["youtube"].startswith("https://www.youtube.com/"), "番組の記録: ほかの欄は、そのまま")
    check(len(data) == 2, "番組の記録: 新しい番組を足す")
    check(tver.update_programs(pp, {"Hello! Music Lab。"}) is False, "番組の記録: 変わらなければ、書かない")
    data[key]["tver"] = "https://tver.jp/series/srlnqjxe9p"
    json.dump({"programs": data}, open(pp, "w", encoding="utf-8"), ensure_ascii=False)
    check(tver.update_programs(pp, {"Hello! Music Lab。"}) is False and json.load(open(pp, encoding="utf-8"))["programs"][key]["tver"].endswith("/series/srlnqjxe9p"), "番組の記録: 手で入れた番組のページ（series）は、検索のページで置き換えない")

print(f"\nall {ok} ok")

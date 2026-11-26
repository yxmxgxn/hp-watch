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

# 毎週の番組: 地方局の遅れ放送（放送日の WEEKLY_BACK 日前までの、いちばん新しいエピソード。前の週の同じ回は選ばない）
B = tver.WEEKLY_BACK
rowsw = [tver.parse_row("/episodes/e1", "Hello! Music Lab。\nx\nテレ東 11月16日(月)放送分\n"),
         tver.parse_row("/episodes/e0", "Hello! Music Lab。\nx\nテレ東 11月9日(月)放送分\n")]
check(B == 6, "遅れ放送を拾う日数は6日（前の週の同じ回は7日前）")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 18)) is None, "単発は、2日以上ずれたら選ばない（水曜の遅れ放送には、選ばない）")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 18), back=B) == "/episodes/e1", "毎週の番組は、水曜の遅れ放送に、月曜のエピソードを選ぶ")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 16), back=B) == "/episodes/e1", "毎週の番組でも、同じ日のエピソードがあれば、それを選ぶ")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 23), back=B) is None, "毎週の番組は、ちょうど7日前（前の週の回）を、選ばない（公開期間が切れて、見られない）")
check(tver.pick(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 25), back=B) is None, "毎週の番組でも、7日以上前のエピソードは選ばない")
# 杜の都musicSPACE: 11/24（火）の回に、前の週（11/17）の回を選んでいた
rows_m = [tver.parse_row("/episodes/old", "杜の都musicSPACE\nx\n仙台放送 11月17日(火)放送分\n"),
          tver.parse_row("/episodes/new", "杜の都musicSPACE\nx\n仙台放送 11月24日(火)放送分\n")]
check(tver.pick_match(rows_m, "杜の都musicSPACE", dt.date(2026, 11, 24), back=B) == ("/episodes/new", True), "その日の回があれば、それを選ぶ（完全に合った＝exact）")
check(tver.pick_match(rows_m[:1], "杜の都musicSPACE", dt.date(2026, 11, 24), back=B) == (None, False), "その日の回がまだ無いとき、前の週の回は選ばない（昔は選んでいた）")
check(tver.pick_match(rowsw, "Hello! Music Lab。", dt.date(2026, 11, 18), back=B) == ("/episodes/e1", False), "遅れ放送で選んだものは、exact ではない（本当の回が上がったら、差し替える）")

# 前後1日は「近い日の回」（深夜の日付のずれ・TVer より後に放送する局）。同じ日の回だけが exact
rows_d = [tver.parse_row("/episodes/prev", "番組X\nx\nA局 11月23日(月)放送分\n"), tver.parse_row("/episodes/next", "番組X\nx\nA局 11月25日(水)放送分\n")]
d24 = dt.date(2026, 11, 24)
check(tver.pick_match(rows_d[:1], "番組X", d24) == ("/episodes/prev", False) and tver.pick_match(rows_d[1:], "番組X", d24) == ("/episodes/next", False), "前の日・次の日の回は、選ぶが、exact ではない（近い日の回）")
check(tver.pick_match(rows_d, "番組X", d24)[0] == "/episodes/prev" and tver.pick_match(list(reversed(rows_d)), "番組X", d24)[0] == "/episodes/prev", "前後1日で迷ったら、TVer に先に上がっている前の日の回を選ぶ（並びに関係なく）")
rows_s = rows_d + [tver.parse_row("/episodes/same", "番組X\nx\nA局 11月24日(火)放送分\n")]
check(tver.pick_match(rows_s, "番組X", d24) == ("/episodes/same", True), "同じ日の回があれば、それを選ぶ（exact）")
# 自動の分の書き方: 近い日の回は、名前で分ける
it0 = {"guid": "g1", "air": d24}
e_ex, e_ne = tver.make_entry(it0, "/episodes/a", True, d24), tver.make_entry(it0, "/episodes/b", False, d24)
check(e_ex["rule"] == tver.RULE == 4, "自動の分: 選び方の版を残す（版が違う昔の分は、1回、探し直す）")
check(e_ex["links"][0]["label"] == "TVer" and e_ex["match"] == "exact" and e_ne["links"][0]["label"] == "TVer（近い日の回）" and e_ne["match"] == "near", "自動の分: 同じ日の回は『TVer』、近い日の回は『TVer（近い日の回）』（match も残す）")

# どの回か（ep）: エピソードの題名・日付をリンクに持たせる
r_ep = tver.parse_row("/episodes/z", "杜の都musicSPACE\nBEYOOOOONDSの魅力を石田先輩熱弁＆横山剣さん降臨！\n仙台放送 11月24日(火)放送分\n12月2日(水)0:55 終了予定")
check(r_ep["sub"].startswith("BEYOOOOONDS") and r_ep["title"] == "杜の都musicSPACE" and tver.parse_row("/e", "番組\nA局 11月1日(日)放送分")["sub"] == "", "検索結果の行: エピソードの題名も持つ（放送日の行は、題名にしない）")
row_p, d_p, ex_p = tver.pick_row([r_ep], "杜の都musicSPACE", dt.date(2026, 11, 25), back=6)
check(row_p["href"] == "/episodes/z" and d_p == dt.date(2026, 11, 24) and ex_p is False, "選んだ行と、その回の日付も返す（前の日の回＝近い日）")
e_ep = tver.make_entry({"guid": "g", "air": dt.date(2026, 11, 25)}, "/episodes/z", False, dt.date(2026, 11, 25), d_p, r_ep["sub"])
check(e_ep["links"][0]["ep"] == {"date": "2026-11-24", "kind": "air", "title": r_ep["sub"][:60]} and "ep" not in tver.make_entry({"guid": "g", "air": dt.date(2026, 11, 25)}, "/episodes/z", True, dt.date(2026, 11, 25))["links"][0], "自動の分: リンクに、どの回か（日付・題名）を持つ（無ければ付けない）")

# 毎週の番組を、最速の局の放送の日時で、回に決める（西乃風ブラン堂: MBS が最速。MBS より前の放送は、1つ前の回）
D = dt.date
rows_n = [tver.parse_row("/episodes/e215", "西乃風ブラン堂\n＃215「温泉」\nMBS 11月23日(月)放送分\n配信終了まで1週間以上"),
          tver.parse_row("/episodes/e214", "西乃風ブラン堂\n＃214「ホテル」\nMBS 11月16日(月)放送分\n配信終了まで1週間以上"),
          tver.parse_row("/episodes/e213", "西乃風ブラン堂\n＃213「港町」\nMBS 11月9日(月)放送分\n12月1日(火)0:58 終了予定")]
check(rows_n[2]["ends"] == (12, 1, 0, 58) and "ends" not in rows_n[0], "検索結果の行: 配信の終了予定を読む（『1週間以上』は無し）")
O = lambda air, st, station: {"guid": f"{station}{air}", "air": air, "start": st, "station": station, "program": "西乃風ブラン堂", "weekly": True, "label": "毎週月曜日"}
ctx_n = [O(D(2026, 11, 23), "24:59", "MBS"), O(D(2026, 11, 16), "24:59", "MBS"), O(D(2026, 11, 23), "10:58", "RCCテレビ"),
         O(D(2026, 11, 22), "05:45", "RSK山陽放送"), O(D(2026, 11, 24), "25:00", "テレビ山口"), O(D(2026, 11, 25), "24:26", "テレビ高知")]
NOW = dt.datetime(2026, 11, 25, 12, 0)
cp = lambda occ: tver.cycle_pick(rows_n, "西乃風ブラン堂", occ, ctx_n, NOW)
r_mbs = cp(ctx_n[0])
check(r_mbs[0]["href"] == "/episodes/e215" and r_mbs[2] is True, "最速の局（MBS）の放送そのものは、その回（exact）")
r_rcc = cp(ctx_n[2])
check(r_rcc[0]["href"] == "/episodes/e214" and r_rcc[2] is False, "MBS の放送（11/24 0:59）より前の放送（RCC 11/23 10:58）は、1つ前の回（#214）。同じ日付でも、時刻で分ける")
check(cp(ctx_n[3])[0]["href"] == "/episodes/e214", "MBS より前の放送（RSK 11/22）は、1つ前の回")
check(cp(ctx_n[4])[0]["href"] == "/episodes/e215" and cp(ctx_n[5])[0]["href"] == "/episodes/e215", "MBS より後の放送（テレビ山口・テレビ高知）は、その回（近い日の回）")
r_next = cp(O(D(2026, 11, 30), "24:59", "MBS"))
check(r_next[0]["href"] == "/episodes/e215" and r_next[3] >= tver.PREV_GAP, "来週の回がまだ上がっていないとき、最新の回との間隔が1週間以上になる（先週分にする）")
check(tver.cycle_pick(rows_n, "西乃風ブラン堂", O(D(2026, 11, 2), "05:00", "RSK山陽放送"), ctx_n, NOW) is None, "どの回より前の放送には、回が無い")
soon = [tver.parse_row("/episodes/old", "西乃風ブラン堂\nx\nMBS 11月16日(月)放送分\n11月25日(水)20:00 終了予定")]
check(tver.cycle_pick(soon, "西乃風ブラン堂", ctx_n[0], ctx_n, NOW) is None, "配信がまもなく終わる回は選ばない（開いたら、見られない）")
check(tver.air_time(D(2026, 11, 23), "24:59") == dt.datetime(2026, 11, 24, 0, 59) and tver.air_time(D(2026, 11, 23), "") is None, "放送の日時: 24:59 は翌日の0:59。開始が無ければ None")
check(tver.same_station("MBS", "MBS") and tver.same_station("テレビ東京系", "テレビ東京") and not tver.same_station("MBS", "RCCテレビ"), "局の名前の合わせ方")
check(not tver.same_station("テレ東", "BSテレ東") and tver.same_station("BSテレ東", "BSテレ東") and tver.same_station("BS12", "BS12 トゥエルビ"), "BS の局と、地上波の局は、別の局（テレ東とBSテレ東）")
e_pv = tver.make_entry({"guid": "g", "air": D(2026, 11, 25)}, "/episodes/e215", False, D(2026, 11, 25), D(2026, 11, 18), "x", 7.0)
e_pl = tver.make_entry({"guid": "g", "air": D(2026, 11, 27)}, "/episodes/e215", False, D(2026, 11, 27), D(2026, 11, 13), "x", 14.0)
check(e_pv["links"][0]["label"] == "TVer（先週分）" and e_pv["match"] == "prev" and e_pl["links"][0]["label"] == "TVer（前の回）" and e_pl["match"] == "prev", "直前の回: 1週間前後は『TVer（先週分）』、10日以上前は『TVer（前の回）』（match: prev）")
with tempfile.TemporaryDirectory() as d4:
    p4 = os.path.join(d4, "programs.json")
    json.dump({"programs": {"TV|a": {"name": "カナフルTV", "cat": "TV", "no_tver": True}, "TV|b": {"name": "キタコイ", "cat": "TV"}}}, open(p4, "w", encoding="utf-8"))
    check(tver.no_tver_programs(p4) == {tver.norm("カナフルTV")} and tver.no_tver_programs(os.path.join(d4, "x.json")) == set(), "TVer に無い番組（no_tver）は、探さない")

# 番組のページ（series）: エピソードのページから取る
check(tver.parse_series('<a href="/series/sr1ea8bikm">番組TOP</a><a href="/series/srzzzzzzzz">x</a>') == "https://tver.jp/series/sr1ea8bikm", "番組のページ: エピソードのページから、最初の series を取る")
check(tver.parse_series("<p>公開期間外です。</p>") is None and tver.parse_series("") is None and tver.parse_series(None) is None, "番組のページ: 公開期間外のページ（series が無い）は None")

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

# 番組の記録（manual/programs.json）: TVer にある番組の、番組のページ（series）だけを残す。検索のページは使わない
S1, S2 = "https://tver.jp/series/sr1ea8bikm", "https://tver.jp/series/srs4nx051o"
with tempfile.TemporaryDirectory() as d2:
    pp = os.path.join(d2, "programs.json")
    key = tver.rss.program_key("TV", "Hello! Music Lab。")
    keym = tver.rss.program_key("TV", "杜の都musicSPACE")
    keyx = tver.rss.program_key("TV", "ふるさとの未来")
    json.dump({"programs": {
        key: {"name": "Hello! Music Lab。", "cat": "TV", "youtube": "https://www.youtube.com/@x"},
        keym: {"name": "杜の都musicSPACE", "cat": "TV", "tver": "https://tver.jp/search/x"},   # 昔の検索のページ
    }}, open(pp, "w", encoding="utf-8"))
    load = lambda: json.load(open(pp, encoding="utf-8"))["programs"]
    check(tver.update_programs(pp, {"ふるさとの未来": S1}) is True and load()[keyx]["tver"] == S1, "番組の記録: 番組のページを足す（新しい番組）")
    check(tver.update_programs(pp, {"杜の都musicSPACE": S2}) is True and load()[keym]["tver"] == S2, "番組の記録: 昔の検索のページは、番組のページに差し替える")
    check(tver.update_programs(pp, {"Hello! Music Lab。": S1}) is True and load()[key]["tver"] == S1 and load()[key]["youtube"].startswith("https://www.youtube.com/"), "番組の記録: ほかの欄は、そのまま")
    check(tver.update_programs(pp, {"ふるさとの未来": S1}) is False, "番組の記録: 変わらなければ、書かない")
    check(tver.update_programs(pp, {"ふるさとの未来": S2}) is False and load()[keyx]["tver"] == S1, "番組の記録: すでに番組のページがあれば（手で入れたものを含む）、置き換えない")
    check(tver.has_series(pp, "ふるさとの未来") is True and tver.has_series(pp, "知らない番組") is False, "番組の記録: 番組のページが有るか")
    # 取れなかったとき: 新しい番組は足さない・昔の検索のページは外す（TVer にあるかどうか区別がつかないので）
    json.dump({"programs": {keym: {"name": "杜の都musicSPACE", "cat": "TV", "tver": "https://tver.jp/search/x", "youtube": "y"}}}, open(pp, "w", encoding="utf-8"))
    check(tver.update_programs(pp, {"新番組": None, "杜の都musicSPACE": None}) is True and "tver" not in load()[keym] and load()[keym]["youtube"] == "y" and len(load()) == 1, "番組の記録: 番組のページが取れなければ、新しい番組は足さず、昔の検索のページは外す")

# 昔の検索のページのままの番組
with tempfile.TemporaryDirectory() as d3:
    p3 = os.path.join(d3, "programs.json")
    json.dump({"programs": {"TV|a": {"name": "キタコイ", "cat": "TV", "tver": "https://tver.jp/search/x"},
                            "TV|b": {"name": "杜の都musicSPACE", "cat": "TV", "tver": "https://tver.jp/series/srs4nx051o"},
                            "TV|c": {"name": "メモだけ", "cat": "TV"}}}, open(p3, "w", encoding="utf-8"))
    check(tver.legacy_search_programs(p3) == ["キタコイ"] and tver.legacy_search_programs(os.path.join(d3, "none.json")) == [], "昔の検索のページのままの番組だけを拾う")
check(tver.tver_title_matches("キタコイ", "キタコイ") and tver.tver_title_matches("杜の都musicSPACE", "杜の都musicSPACE") and not tver.tver_title_matches("別の番組", "キタコイ"), "検索結果の番組名が、番組名と合うか")

# 自動の分: 探し直して合わなかった分は外す
today2 = dt.date(2026, 11, 25)
old2 = {"g-bad": {"guid": "g-bad", "air": "2026-11-24", "until": "2026-11-27", "links": [{"label": "TVer", "url": "https://tver.jp/episodes/old"}]},
        "g-keep": {"guid": "g-keep", "air": "2026-11-24", "until": "2026-11-27", "links": [{"label": "TVer", "url": "https://tver.jp/episodes/k"}], "match": "exact"}}
check([e["guid"] for e in tver.merge_auto(old2, {}, today2, drop={"g-bad"})] == ["g-keep"], "自動の分: 探し直して合わなかった分（前の週の回など）は外す")

print(f"\nall {ok} ok")

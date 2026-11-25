"""episodes.py の確かめ（ネットワークなし）。  python3 test_episodes.py"""
import datetime as dt

import copy

import episodes as ep

ok = 0


def check(cond, msg):
    global ok
    if not cond:
        raise SystemExit(f"NG: {msg}")
    ok += 1
    print("ok:", msg)


D = dt.date

# --- 題名の日付
check(ep.title_dates("#06 Juice=Juice ジューシー・トーク（2026年11月15日放送分）", D(2026, 11, 23)) == {D(2026, 11, 15)}, "題名の日付: 年月日")
check(ep.title_dates("11/16 ゲストは稲垣悠一郎さん", D(2026, 11, 23)) == {D(2026, 11, 16)}, "題名の日付: 月/日（年は公開日にいちばん近い年）")
check(ep.title_dates("12/28 年末", D(2027, 1, 4)) == {D(2026, 12, 28)}, "題名の日付: 年をまたぐ")
check(ep.title_dates("＃２１６　募金箱は僕が持ちますよ", D(2026, 11, 16)) == set(), "題名の日付: 日付が無ければ空（番号は日付ではない）")

# --- RSS
RSS = """<?xml version="1.0"?><rss><channel><title>t</title>
<item><title>#06 ジューシー・トーク（2026年11月15日放送分）</title><link>https://omny.fm/shows/x/06</link><pubDate>Mon, 23 Nov 2026 03:00:00 +0000</pubDate></item>
<item><title><![CDATA[#216 募金箱]]></title><enclosure url="https://cdn.example/216.mp3"/><pubDate>Mon, 16 Nov 2026 14:00:00 +0000</pubDate></item>
<item><title>壊れた日付</title><pubDate>not a date</pubDate></item>
</channel></rss>"""
eps = ep.parse_rss(RSS)
check(len(eps) == 2 and eps[0]["date"] == D(2026, 11, 23), "RSS: 日付が読めない回は捨てる・新しい順")
check(eps[1]["date"] == D(2026, 11, 16) and eps[1]["link"] == "" and eps[1]["audio"].endswith("216.mp3"), "RSS: 公開日は日本時間（UTC 14:00 は、翌日の23:00 JST ではなく同日 23:00）。リンクが無ければ音声")
check(ep.episode_link(eps[1]).endswith("216.mp3") and ep.episode_link(eps[0]).startswith("https://omny.fm"), "リンク: エピソードのページ、無ければ音声のURL")

# --- ポッドキャストの結びつけ
juice = [{"title": "#06 ジューシー・トーク（2026年11月15日放送分）", "link": "https://omny.fm/06", "audio": "", "date": D(2026, 11, 23)},
         {"title": "#05 ジューシー・トーク（2026年11月8日放送分）", "link": "https://omny.fm/05", "audio": "", "date": D(2026, 11, 16)}]
check(ep.match_podcast(juice, D(2026, 11, 15))["link"].endswith("/06"), "放送日の8日あとの公開でも、題名の日付で結びつく")
check(ep.match_podcast(juice, D(2026, 11, 22)) is None, "題名に別の日付があるエピソードは、日付が違う放送日には選ばない")
plain = [{"title": "＃２１６　募金箱", "link": "https://a/216", "audio": "", "date": D(2026, 11, 16)},
         {"title": "＃２１５　タクシー", "link": "https://a/215", "audio": "", "date": D(2026, 11, 9)}]
check(ep.match_podcast(plain, D(2026, 11, 16))["link"].endswith("/216"), "日付が無い題名は、放送日の当日の公開を選ぶ")
check(ep.match_podcast(plain, D(2026, 11, 9))["link"].endswith("/215"), "日付が無い題名は、放送日に公開日がいちばん近いものを選ぶ")
check(ep.match_podcast(plain, D(2026, 11, 17))["link"].endswith("/216"), "同じ週の、別の曜日の放送（再放送）には、同じエピソードを選ぶ（前の日の公開）")
check(ep.match_podcast(plain, D(2026, 11, 23)) is None, "最新の公開から7日あとの放送は、まだ新しいエピソードが無いので、選ばない")
check(ep.match_podcast(plain, D(2026, 11, 14))["link"].endswith("/216"), "公開が放送日より後のほうが近ければ、後のものを選ぶ（遅れて上がる番組）")

# --- YouTube
YT = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>a</yt:videoId>
<title>【ハロ！ステ#500】MC 小林萌花</title><link rel="alternate" href="https://www.youtube.com/watch?v=aaa"/><published>2026-11-18T10:00:12+00:00</published></entry>
<entry><title>Coming soon #佐藤優樹</title><link rel="alternate" href="https://www.youtube.com/watch?v=bbb"/><published>2026-11-18T09:00:00+00:00</published></entry>
<entry><title>【ハロ！ステ#499】前の週</title><link rel="alternate" href="https://www.youtube.com/watch?v=ccc"/><published>2026-11-11T08:02:19+00:00</published></entry></feed>"""
vids = ep.parse_youtube(YT)
check(len(vids) == 3 and vids[0]["date"] == D(2026, 11, 18), "YouTube: 公開は日本時間の日付（UTC 10:00 は 19:00 JST・同じ日）")
check(ep.match_youtube(vids, ["ハロ！ステ"], D(2026, 11, 18))["link"].endswith("aaa"), "YouTube: 放送日に公開された、題名に番組名がある動画")
check(ep.match_youtube(vids, ["ハロ！ステ"], D(2026, 11, 19)) is None, "YouTube: 放送日に公開が無ければ、選ばない")
check(ep.match_youtube(vids, ["M-line Music"], D(2026, 11, 18)) is None, "YouTube: 題名に番組名が無い動画は、選ばない（Coming soon を選ばない）")

# テレビの番組が、放送の数日後に、題名に放送日を書いて上がる（ええじゃないか！！）
YT2 = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><title>ええじゃないか！！# 201　11月22日放送回</title><link rel="alternate" href="https://www.youtube.com/watch?v=e201"/><published>2026-11-24T05:15:16+00:00</published></entry>
<entry><title>【TOKYO GAME SHOW 2026】加藤ナナちゃんがロケで会場を楽しくめぐりました！！</title><link rel="alternate" href="https://www.youtube.com/watch?v=cm"/><published>2026-11-24T04:55:08+00:00</published></entry>
<entry><title>ええじゃないか！！# 200　11月8日放送回</title><link rel="alternate" href="https://www.youtube.com/watch?v=e200"/><published>2026-11-18T05:06:45+00:00</published></entry></feed>"""
v2 = ep.parse_youtube(YT2)
check(ep.match_youtube(v2, ["ええじゃないか"], D(2026, 11, 22))["link"].endswith("e201"), "YouTube: 題名の日付（11月22日放送回）が放送日と合う動画を選ぶ（公開は2日後）")
check(ep.match_youtube(v2, ["ええじゃないか"], D(2026, 11, 24)) is None, "YouTube: 公開日が放送日でも、題名に別の日付があれば選ばない・番組名が無い動画（CM）も選ばない")
check(ep.match_youtube(v2, ["ええじゃないか"], D(2026, 11, 8))["link"].endswith("e200"), "YouTube: 前の回は、前の回の放送日に結びつく")

# --- 書き方
old = [{"guid": "g1", "air": "2026-11-01", "until": "2026-11-15", "links": [{"label": "ポッドキャスト", "url": "https://x/1"}]},
       {"guid": "g2", "air": "2026-11-20", "until": "2026-12-04", "links": [{"label": "ポッドキャスト", "url": "https://x/2"}]}]
new = [{"guid": "g2", "air": "2026-11-20", "until": "2026-12-04", "links": [{"label": "YouTube", "url": "https://y/2"}, {"label": "ポッドキャスト", "url": "https://x/2"}]},
       {"guid": "g3", "air": "2026-11-23", "until": "2026-12-07", "links": [{"label": "YouTube", "url": "https://y/3"}]}]
m = ep.merge_entries(old, new, D(2026, 11, 24))
check([e["guid"] for e in m] == ["g2", "g3"], "書き方: 期限の切れた分は外す")
check([l["url"] for l in m[0]["links"]] == ["https://x/2", "https://y/2"], "書き方: 同じ項目は、リンクをまとめる（同じURLは重ねない）")

# どの回か（ep）
info = ep.ep_info({"title": "#06 Juice=Juice ジューシー・トーク（2026年11月15日放送分）", "date": D(2026, 11, 23)})
check(info == {"date": "2026-11-23", "kind": "pub", "title": "#06 Juice=Juice ジューシー・トーク（2026年11月15日放送分）", "titled": "2026-11-15"}, "どの回か: 公開日・題名・題名の日付（あれば）")
check("titled" not in ep.ep_info({"title": "#195 話", "date": D(2026, 11, 20)}), "どの回か: 題名に日付が無ければ、titled は付けない")
old_e = [{"guid": "g1", "air": "2026-11-15", "until": "2026-12-01", "links": [{"label": "ポッドキャスト", "url": "https://x/a"}]}]
new_e = [{"guid": "g1", "air": "2026-11-15", "until": "2026-12-01", "links": [{"label": "ポッドキャスト", "url": "https://x/a", "ep": info}, {"label": "YouTube", "url": "https://x/y"}]}]
merged = ep.merge_entries(old_e, new_e, D(2026, 11, 24))
check(len(merged) == 1 and merged[0]["links"][0]["ep"] == info and len(merged[0]["links"]) == 2, "昔の分にも、同じURLなら、どの回か（ep）を足す。新しいURLは足す")

# BOOKSTAND.TV: 番組専用のチャンネル。題名はテーマの文で、説明欄の1行目に回が書いてある
YT3 = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:media="http://search.yahoo.com/mrss/">
<entry><title>“これ『が』いい”ではなく“これ『で』いい”！</title><link rel="alternate" href="https://www.youtube.com/watch?v=v94"/><published>2026-11-24T09:08:00+00:00</published>
<media:group><media:description>BOOKSTAND.TV YouTube版 #94
本文…</media:description></media:group></entry>
<entry><title>【文章の書き方に正解はあるのか？】</title><link rel="alternate" href="https://www.youtube.com/watch?v=v453"/><published>2026-11-18T01:00:00+00:00</published>
<media:group><media:description>2026年11月17日放送 #453</media:description></media:group></entry></feed>"""
v3 = ep.parse_youtube(YT3)
check(v3[0]["desc"] == "BOOKSTAND.TV YouTube版 #94" and v3[1]["desc"] == "2026年11月17日放送 #453", "YouTube: 説明欄の1行目を読む")
check(ep.match_youtube(v3, ["BOOKSTAND"], D(2026, 11, 24)) is None, "番組名が題名に無い動画は、専用のチャンネルでなければ選ばない")
check(ep.match_youtube(v3, ["BOOKSTAND"], D(2026, 11, 24), dedicated=True)["link"].endswith("v94"), "専用のチャンネル: 11/24（再放送の夜）には、同じ日に公開された『YouTube版』を選ぶ")
check(ep.match_youtube(v3, ["BOOKSTAND"], D(2026, 11, 17), dedicated=True)["link"].endswith("v453"), "専用のチャンネル: 11/17 の放送には、説明欄に『2026年11月17日放送』とある動画を選ぶ（公開は翌朝の11/18）")
check(ep.match_youtube(v3, ["BOOKSTAND"], D(2026, 11, 18), dedicated=True) is None, "専用のチャンネル: 別の日付（11/17放送）と書いてある動画は、公開日の11/18には選ばない")
check([ep.youtube_label(v) for v in v3] == ["YouTube版", "YouTube（放送分）"] and ep.youtube_label({"title": "x", "date": D(2026, 11, 1), "desc": ""}) == "YouTube", "リンクの名前: YouTube版／YouTube（放送分）／YouTube")
check(ep.ep_info(v3[1])["note"] == "2026年11月17日放送 #453" and ep.ep_info(v3[1])["titled"] == "2026-11-17", "どの回か: 説明欄の1行目（note）と、そこに書かれた日付（titled）")

# 今週の回がまだ無いとき: 直前の回を『先週分』として付ける（Juice=Juice: ポッドキャストは、放送の約1週間後に出る）
J = lambda n, titled, pub: {"title": f"#0{n} Juice=Juice ジューシー・トーク（{titled.year}年{titled.month}月{titled.day}日放送分）", "link": f"https://omny.fm/{n}", "audio": "", "date": pub}
eps_j = [J(6, D(2026, 11, 15), D(2026, 11, 23)), J(5, D(2026, 11, 8), D(2026, 11, 16)), J(4, D(2026, 11, 1), D(2026, 11, 9))]
pv = ep.prev_podcast(eps_j, D(2026, 11, 22))
check(pv[0]["link"].endswith("/6") and pv[1] == D(2026, 11, 15), "先週分: 11/22 の放送には、まだ出ていない今週の回の代わりに、直前の回（題名の日付が 11/15 の #06）")
check(ep.prev_podcast(eps_j, D(2026, 11, 15))[0]["link"].endswith("/5") and ep.prev_podcast(eps_j, D(2026, 10, 1)) is None, "先週分: その放送より前の回だけ（11/15 の放送には、11/8 の回）。14日より前の回しか無ければ付けない")
check(ep.prev_label("ポッドキャスト", 7) == "ポッドキャスト（先週分）" and ep.prev_label("ポッドキャスト", 14) == "ポッドキャスト（前の回）", "先週分: 10日までは『先週分』、それより前は『前の回』")
check(ep.ep_info(eps_j[0], prev=True)["prev"] is True and "prev" not in ep.ep_info(eps_j[0]), "どの回か: 先週分の印（prev）")
old_p = [{"guid": "g22", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "ポッドキャスト（先週分）", "url": "https://omny.fm/6", "ep": {"prev": True, "date": "2026-11-23"}}]}]
real = [{"guid": "g22", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "ポッドキャスト", "url": "https://omny.fm/7", "ep": {"date": "2026-12-01"}}]}]
m1 = ep.merge_entries(copy.deepcopy(old_p), real, D(2026, 11, 30))
check([l["url"] for l in m1[0]["links"]] == ["https://omny.fm/7"], "本当の回が出たら、昔の『先週分』は外れる")
newer = [{"guid": "g22", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "ポッドキャスト（先週分）", "url": "https://omny.fm/6b", "ep": {"prev": True}}]}]
m2 = ep.merge_entries(copy.deepcopy(old_p), newer, D(2026, 11, 30))
check([l["url"] for l in m2[0]["links"]] == ["https://omny.fm/6b"], "直前の回が変わったら、昔の『先週分』は、新しい『先週分』に替わる")
yt_keep = [{"guid": "g22", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "YouTube", "url": "https://y/1"}, {"label": "ポッドキャスト（先週分）", "url": "https://omny.fm/6", "ep": {"prev": True}}]}]
m3 = ep.merge_entries(copy.deepcopy(yt_keep), real, D(2026, 11, 30))
check(sorted(l["url"] for l in m3[0]["links"]) == ["https://omny.fm/7", "https://y/1"], "ほかの種類のリンク（YouTube）は、残る")

# YouTube: 今週の動画がまだ無いとき、直前の動画を付ける（月2回の番組は『前の回』）
V = lambda link, pub, title="ええじゃないか！！# 200　11月8日放送回": {"title": title, "link": link, "date": pub, "desc": ""}
vids_e = [V("https://y/201", D(2026, 11, 24), "ええじゃないか！！# 201　11月22日放送回"), V("https://y/200", D(2026, 11, 18)), V("https://y/199", D(2026, 10, 25), "ええじゃないか！！#199 　10月25日放送回")]
pvv = ep.prev_video(vids_e, ["ええじゃないか"], D(2026, 11, 22))
check(pvv[0]["link"] == "https://y/200" and pvv[1] == D(2026, 11, 8), "YouTube: 11/22 の動画がまだ無いとき、直前の動画（題名の日付が 11/8 の #200）")
check(ep.prev_video(vids_e, ["ええじゃないか"], D(2026, 12, 13))[0]["link"] == "https://y/201" and ep.prev_video(vids_e, ["ええじゃないか"], D(2026, 12, 20)) is None, "YouTube: 21日より前の動画しか無ければ付けない")
check(ep.prev_video([V("https://y/x", D(2026, 11, 20), "別の番組の動画")], ["ええじゃないか"], D(2026, 11, 22)) is None and ep.prev_video([V("https://y/x", D(2026, 11, 20), "別の番組の動画")], ["ええじゃないか"], D(2026, 11, 22), dedicated=True) is not None, "YouTube: 番組名が題名に無い動画は、専用のチャンネルでなければ付けない")
check(ep.label_base("ポッドキャスト（先週分）") == "ポッドキャスト" and ep.label_base("YouTube版（前の回）") == "YouTube" and ep.label_base("YouTube（放送分）") == "YouTube" and ep.label_base("YouTube") == "YouTube", "リンクの名前の、種類の部分")
old_y = [{"guid": "g1", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "YouTube版（前の回）", "url": "https://y/200", "ep": {"prev": True}}]}]
new_y = [{"guid": "g1", "air": "2026-11-22", "until": "2026-12-06", "links": [{"label": "YouTube（放送分）", "url": "https://y/201", "ep": {"date": "2026-11-24"}}]}]
check([l["url"] for l in ep.merge_entries(copy.deepcopy(old_y), new_y, D(2026, 11, 25))[0]["links"]] == ["https://y/201"], "YouTube: 本当の動画が出たら、昔の『前の回』（名前が『YouTube版』でも）は外れる")

print(f"\nall {ok} ok")

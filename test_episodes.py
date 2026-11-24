"""episodes.py の確かめ（ネットワークなし）。  python3 test_episodes.py"""
import datetime as dt

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

print(f"\nall {ok} ok")

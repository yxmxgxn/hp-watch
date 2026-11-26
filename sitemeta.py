"""サイトの <head> に入れる、ファビコン・共有したときの画像（OGP）・色。どのページも同じ画像・同じ色。

画像は static/ に置く（rss.py が、公開のたびに、サイトの一番上へコピーする）:
  favicon.ico（16・32・48）・icon-192.png・icon-512.png・apple-touch-icon.png（180）・og.png（1200x630）
"""
from urllib.parse import quote
from xml.sax.saxutils import escape, quoteattr

SITE_URL = "https://hello-rss.karin-archive.com"   # rss.py が、--feed-base で上書きする
SITE_NAME = "ハロプロ RSS アンオフィシャル"
TAGLINE = "ハロー！プロジェクト関連のお知らせをRSSで読む"
DESCRIPTION = "ハロー！プロジェクト関連のお知らせをRSSで読む。公式サイトの情報を RSS にしたものです。リーダーにURLを登録して使います。"
THEME_COLOR = "#0080cb"   # ハロプロ公式の青


def icon_tags():
    """ファビコン・ホーム画面のアイコン・ブラウザの色。画像は、サイトの一番上（/）にある"""
    return (
        '<link rel="icon" href="/favicon.ico" sizes="any">'
        '<link rel="icon" type="image/png" sizes="192x192" href="/icon-192.png">'
        '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
        f'<meta name="theme-color" content="{THEME_COLOR}">'
    )


def head_tags(title, description=DESCRIPTION, path="/", site_url=None):
    """<title>・説明・ファビコン・OGP（Twitter／X のカードも）。title はページごと、画像はどのページも og.png"""
    base = (site_url or SITE_URL).rstrip("/")
    page = base + path
    image = base + "/og.png"
    q = quoteattr
    return (
        f"<title>{escape(title)}</title>"
        f"<meta name=\"description\" content={q(description)}>"
        + icon_tags() +
        f"<link rel=\"canonical\" href={q(page)}>"
        '<meta property="og:type" content="website">'
        '<meta property="og:locale" content="ja_JP">'
        f"<meta property=\"og:site_name\" content={q(SITE_NAME)}>"
        f"<meta property=\"og:title\" content={q(title)}>"
        f"<meta property=\"og:description\" content={q(description)}>"
        f"<meta property=\"og:url\" content={q(page)}>"
        f"<meta property=\"og:image\" content={q(image)}>"
        '<meta property="og:image:width" content="1200">'
        '<meta property="og:image:height" content="630">'
        f"<meta property=\"og:image:alt\" content={q(SITE_NAME)}>"
        '<meta name="twitter:card" content="summary_large_image">'
        f"<meta name=\"twitter:title\" content={q(title)}>"
        f"<meta name=\"twitter:description\" content={q(description)}>"
        f"<meta name=\"twitter:image\" content={q(image)}>"
    )


FOOTER_CSS = """
.site-foot{margin-top:40px;padding-top:14px;border-top:1px solid var(--line);color:var(--mute);font-size:.85rem}
.site-foot p{margin:4px 0;color:var(--mute)}
.site-foot .share a{margin:0 4px}
.site-foot .share button{font:inherit;font-size:.85rem;color:var(--link);background:transparent;border:1px solid var(--line);border-radius:6px;padding:1px 10px;margin-left:4px;cursor:pointer}
.site-foot .share button:hover{border-color:var(--accent)}
"""


def footer_html():
    """どのページも同じフッター（手で入れるページは除く）。一覧・選んで作る RSS・このサイトについて、への行き来と、ご連絡の入口"""
    top = SITE_URL.rstrip("/") + "/"
    x = "https://x.com/intent/post?text=" + quote(SITE_NAME, safe="") + "&url=" + quote(top, safe="")
    line = "https://social-plugins.line.me/lineit/share?url=" + quote(top, safe="")
    share = (
        '<p class="share">このサイトを紹介する：'
        f'<a href={quoteattr(x)} target="_blank" rel="noopener noreferrer">X</a>'
        f'<a href={quoteattr(line)} target="_blank" rel="noopener noreferrer">LINE</a>'
        f'<button type="button" data-url={quoteattr(top)} '
        "onclick=\"navigator.clipboard.writeText(this.dataset.url).then(()=>{this.textContent='コピーしました'},()=>{this.textContent='コピーできませんでした'})\">URLをコピー</button></p>"
    )
    return (
        '<footer class="site-foot">'
        '<p><a href="/">一覧に戻る</a> ｜ <a href="/today/">今日の予定</a> ｜ <a href="/custom/">選んで作る RSS</a> ｜ <a href="/about/">このサイトについて</a></p>'
        + share +
        "<p>題名とリンクだけを配ります。本文は、公式のページでお読みください。</p>"
        '<p>個人が作った非公式のサイトです。<a href="/about/#contact">ご連絡はこちら</a>。</p>'
        "</footer>"
    )

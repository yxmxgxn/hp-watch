"""サイトの <head> に入れる、ファビコン・共有したときの画像（OGP）・色。どのページも同じ画像・同じ色。

画像は static/ に置く（rss.py が、公開のたびに、サイトの一番上へコピーする）:
  favicon.ico（16・32・48）・icon-192.png・icon-512.png・apple-touch-icon.png（180）・og.png（1200x630）
"""
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

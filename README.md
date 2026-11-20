# hp-watch

ハロー！プロジェクト公式サイトとJPROOMのスケジュールJSONを毎日15時に取得し、前回からの差分をDiscordに通知する。

- 単発の出演: 新規
- レギュラー番組: 開始（毎週の放送分は通知しない）
- イベント: 発表
- 変更（日付・タイトル・備考・中止など）・削除・レギュラー終了は通知しない

## プロフィール差分（profiles.py）

メンバー約90人のプロフィール（特技・趣味・資格・メンバーカラーなど）を毎日取得し、項目ごとの追加 / 変更 / 削除を同じ Discord に通知する。

- ページの新規 / 消滅 / 移動（同名のページが別パスに現れたとき）も通知
- 項目が書かれていない人は普通の状態。後から書かれたら「（なし）→ ○○」で通知
- メンバーカラーは色名と色コード(CSS)を別項目で比較
- 状態は `state/profiles_*.json`。初回は保存のみで通知しない
- 取得に失敗したページは「消えた」とみなさず前回のまま。取れた件数が半分未満に減ったら保存せず失敗にする
- 手元確認: `python profiles.py --parse 保存したページ.html`

## 設定

リポジトリの Settings → Secrets and variables → Actions に `DISCORD_WEBHOOK_URL` を登録する。

## 運用メモ

- 初回の実行は状態ファイル（`state/*.json`）を作るだけで通知しない
- 状態ファイルは差分があったときだけコミットされる
- うるさい番組は `mute.txt` にタイトルの一部（正規表現可）を1行ずつ足す。GitHubのWeb画面から編集してよい
- 判定方法を変えたら `state/` のファイルを消して作り直す（消さないと大量の誤通知が出る）
- 手元で試すとき: `python hpwatch.py --state state/helloproject.json --force`（Webhook未設定なら画面に出る）

## RSS（rss.py・作成中）

公式のJSONから RSS を作る。公開先は `https://hello-rss.karin-archive.com`（Cloudflare Workers `hello-rss`）。

- お知らせ: `hp/news/{グループ}.xml`・`hp/news/all.xml`、`jproom/news/all.xml`
  - タイトルは `2026.11.16 [NEWS] 題名`（公式サイトの並びと日付の書き方）
  - 各フィード最新50件。本文は「グループ・種類・日付」の1行と公式へのリンク（タイトルの繰り返しにしない）
  - 記事の時刻は、こちらが初めて見た時刻（JSONには日付しか無いため）。状態は `state/rss_news_*.json`。初回は今ある分を公式の日付の正午のまま入れる（0時ちょうどだと Inoreader で前日の23:59と表示されたため）。公式の日付が7日より前の記事は、初めて見ても公式の日付のまま
  - リンクは JSON の `link`、無ければ `/news/{id}/`
  - 更新は追わない。載った日に1回配信して終わり（公式の UPDATE の印はタイトルに入れず、カテゴリ欄にだけ残す）
- MEDIA: `hp/media/{グループ}.xml`・`hp/media/all.xml`、`jproom/media/all.xml`
  - スケジュールの TV / RADIO / MAGAZINE / WEB で、新しく載った予定。毎週の番組は出さない
  - 出演は公式のタグどおり（`HELLO! PROJECT`／`BEYOOOOONDS`／`BEYOOOOONDS：平井美葉` など）。今日の予定も同じ
  - 放送日を過ぎてから載ったものは出さない。WEB・雑誌だけ30日以内なら「[掲載済]」で出す
  - ラジオは radiko のタイムフリーのリンク付き（`radiko.py`）。局名から局IDを引き、radiko の週間番組表で開始時刻とタイムフリー可否を確かめる。局名が合わないときは `radiko.py` の `ALIAS` に足す。無料で聴ける範囲（関東・関西・東海・全国、または都道府県）も radiko の都道府県ごとの局一覧から出す
  - 状態は `state/rss_media_*.json`。初回は今日から1週間分を入れて始める
- 今日の予定: `hp/today.xml`、`jproom/today.xml`
  - その日の予定を、毎週の番組も含めて全部、時刻順に1記事にまとめる（記事の時刻は朝5時）
  - 同じ番組・同じ時刻・同じ出演者で局だけ違うものは1行にまとめる。ラジオは radiko のタイムフリーのリンク付き
  - 直近7日分を毎回JSONから作り直す（状態は持たない）
- ファンクラブニュース: `fc/{all,news,event,concert,goods,stream,other}.xml`（全体・ニュース・公式のタブ5つ）と `fc/group/{グループ}.xml`（8グループ）。M-line club のファンクラブ（`upfc.jp/m-line`）も同じ作りで、`mline/{all,news,fcevent,ticket,goods,contents,other}.xml`（タブは公式のもの）。29人分の個人別は静的には作らず、選択式で選ぶ。ファンクラブごとの設定は `rss.py` の `FC_SITES`
  - Hello! Project オフィシャルファンクラブ（`upfc.jp/helloproject`）のHTMLを読む（JSONは無い）。「すべて」の一覧（1ページ15件）の1ページ目を見て、新しく載った記事を足す。初回は2ページ分
  - ニュースは、公式のタブには無いが「すべて」の中にある分類。そこだけ別のフィードにしている
  - グループ別・人別は、そのページの一覧（`news_list.php?gid=…`、1ページ目）に載っているかどうかで決める（記事にはグループ・人の印が無い）。番号（gid）はそのページの「view more」のリンクから読んで、状態に持つ。M-line の人の一覧は、ファンクラブのトップのページから毎回読む（読めなければ前回の分）
  - 更新を見分ける印（`Last-Modified` など）が無いので、`rss.yml` が動くたびに見る（ハロプロの公式の更新を検知したときと毎朝5:00）
  - 日付は公式の書き方（`2026.11.19`）のまま。記事の時刻は、お知らせと同じ決め方（初めて見た時刻、分・秒は0。初回は日付の正午）
  - 状態は `state/rss_fc.json`（M-line は `rss_fc_mline.json`）。取得に失敗しても、前回の状態があればそれでフィードを書く（他のフィードの公開を止めない）。1グループだけ失敗したときは、そのグループだけ飛ばす
- JPROOM・アップフロント（`up-front-create.com`。同じ仕組み・同じ版。会社全体のサイトで、ハロプロ以外の人も入る）は所属が個人なので、どれも全員まとめて1本。人ごとに見たいときは選択式（下）で選ぶ
- `index.html`（一覧）と `all.opml`（まとめて登録）
- 手元で試す: `python rss.py --out public --state-dir /tmp/rss-state`（`state/` を汚さないように）

- 公開: `.github/workflows/rss.yml` が JST 5:07 と 8〜23時の3時間おきに `rss.py` を動かし、`wrangler.toml` で Cloudflare Workers（`hello-rss`）に送る
  - Secrets に `CLOUDFLARE_API_TOKEN`（テンプレート「Edit Cloudflare Workers」、アカウントと `karin-archive.com` に絞る）と `CLOUDFLARE_ACCOUNT_ID` が要る。無い間は公開を飛ばす
  - どちらかのサイトの取得に失敗した回は公開しない（そのサイトのフィードが消えるので）

これから: TVer の番組ページの対応表。

## 選んで作る RSS（`/custom/`）

好きな人・グループ・カテゴリ・言葉だけのフィードを、各自が選んで作れる。選んだ内容はURLに入るので、保存先（KV など）は要らない。

- 選ぶ画面: `https://hello-rss.karin-archive.com/custom/`（トップの一覧からリンク）
- URL: `/custom.xml?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT&q=ツアー&x=抽選`
  - `site`（`hp`・`jproom`・`upfront`・`fc`・`mline`・`helloshop`・`ameblo`・`youtube`）と `kind`（`news`・`schedule`。アメブロは `blog`、YouTube は `video`）は必須。**サイトはまたがない**。**お知らせとスケジュールは別**（1本＝1つのサイト × 片方の種類）。ハロショ・ファンクラブはお知らせ（ニュース）だけ
  - `who`（人・グループ）、`cat`（カテゴリ）は任意。カンマ区切りで複数。同じ項目の中は「どれか」
  - `general`: 特定の人・グループに紐づかない**全体のもの**の扱い。`general=1` は全体のものだけ（`who` とは一緒に使えない）、`general=0` は全体のものを除く（`who=angerme&general=0` で、アンジュルムのタグだけの記事）。無ければ含める。1・0 以外は400。全体のものとは、ハロプロは全グループのタグ（`HELLO! PROJECT`）が付いたものと、どのタグも付いていないもの。JPROOM・アップフロントは誰にも紐づかないもの。ファンクラブはどのグループ・人の一覧にも載っていないもの。ハロショはグループのタグが無いもの（店舗・お知らせなど）
  - `q`（含む言葉）、`x`（含まない言葉）: **題名だけ**が対象（本文は持たない・公開しない）。`q` はスペース区切りで全部を含む、`x` はどれかを含むものを除く。全角・半角・大文字・小文字・異体字（﨑／崎、櫻／桜など）をそろえて探す（`watcher/src/text.js`）。題名の飾り（日付・`[NEWS]`・`[RADIO]`・`[FC限定]` など）は探さない。言葉は5個・60文字まで
  - 上の条件は、全部を満たす記事だけ。空なら全部。最新50件
  - 選べない値は400（誤ったURLを、空のフィードとして登録させない）。データが読めないときは503
- 仕組み: 公開のたびに `rss.py`（`selectable.py`）が `data/{site}-{kind}.json`（人・グループ・カテゴリ・題名付きの記事、最大400件）と `data/options.json`（選べる値）、`custom/index.html` を書く。Worker（`watcher/src/custom.js`）がデータを読んで、条件に合う記事だけを RSS にして返す（`Cache-Control: max-age=600`）。**`data/` は公開されているので、本文など公開したくないものは入れない**
- 人・グループのキーは、公式のプロフィールの `slug`（無い・重複するものは `id{番号}`）。名前が空のプロフィールは選択肢に出さない
- スケジュールの元は「新しく載った出演」（MEDIA のフィードと同じ）。新しく載ったものから溜めるので、使い始めは記事が少なく、日がたつほど増える。人・グループは、記事がまだ無くても選べる
- ハロショ（`helloshop.info`）: 公式が WordPress で RSS（`/feed`）を出している。記事（題名・リンク・公開時刻・カテゴリ）を `state/rss_helloshop.json` に溜める（本文・画像は読まない）。初回は直近30日分（3ページ）、あとは新しい記事だけ。取りに行くときは前回の ETag を付けて、変わっていなければ 304 で済ませる（公式への負担を抑える）。更新は、時計が `/feed` の `Last-Modified` を見て（15分おき）、変わったら `rss.yml` を呼ぶ。タグ・カテゴリの英字のIDは `rss.py` の `SHOP_WHO`・`SHOP_CATS`（公式の `/tag/{ID}/`・`/category/{ID}/`。増えたらそこに足す）
  - 公式のRSSは、ページ（画面）には出ていない。`<head>` に自動検出用の `<link rel="alternate">` があるだけ
  - 見送り: 本文の全文保存（公開してしまう・本文のページを1件ずつ取りに行く負担・検索のノイズ・公式が消した記事が残る）、DVDマガジン（配信）のスケジュール入り、`up-front-promotion.co.jp`
- アメブロ（`site=ameblo&kind=blog`。ハロプロのグループ・メンバーのブログ）: **保存しない**。リーダーが取りに来たときに、Worker（`watcher/src/ameblo.js`）が選んだブログのRSS（`https://rssblog.ameba.jp/{ブログのID}/rss20.xml`。最新30件。`ETag`・`Last-Modified`・カテゴリ（テーマ）は無い）を取りに行き、題名・リンク・公開時刻だけにして返す（本文の抜粋は読まない）。同じRSSは Cloudflare のキャッシュで10分使い回す（何人が使っても、1ブログあたり10分に1回ほど）
  - ブログのIDと、グループ・メンバーとの対応は、公式のプロフィール（`blogSlug`。グループは、そのグループのブログ全部／メンバーは、その人のブログ）から `rss.py` の `ameblo_source` が `options.json` に入れる。手で持つ対応表は無い（7グループ・69人・14ブログ）
  - `who`: グループは、そのグループのブログの記事全部。メンバーは、そのメンバーのブログのうち、**題名にその名前がある記事**（グループのブログは、題名の末尾に書き手の名前を書く書き方が多い。ニックネームだけ・名前を書かない記事は拾えない。絶対ではない）。名前の照合は、全角半角・空白・大文字小文字・異体字（﨑／崎、櫻／桜、澤／沢、嶋／島など。`watcher/src/text.js`）をそろえる。題名の先頭に `[グループ名]` を付ける。`general`（全体のもの）・`cat` は無い（400）
  - **なぜ、ハロプロのブログだけか**: JPROOM・アップフロントは1人に1つのブログなので、ブログ全体＝その人の公式のRSSで足りる（そのURLをトップの一覧に載せている）。ハロプロのグループのブログは、複数のメンバーの記事が混ざっていて、テーマ（カテゴリ）でも分かれていない。人で絞るための仕組みが要るのは、ここだけ。ブログ全体を見たいだけなら、公式のRSSを直接使うほうが確実なので、全体用のフィードは作らない
  - **なぜ、保存しないか**: 更新の印が無く、見張るには全ブログを取りに行くしかない（更新のあるブログ全部で1日に約57記事・RSSの合計1.6MB）。保存する利点（さかのぼれる）より、動かす回数の負担のほうが大きい。リーダーが取りに来る間隔なら、最新30件で足りる（アンジュルムは約5日分）。ハロショは逆で、更新の印（`ETag`・`Last-Modified`）があり、量も少ない（1日1.2件）ので、保存して溜める
  - 一部のブログのRSSが読めなくても、読めたブログの分は返す。全部読めなければ502
- YouTube（`site=youtube&kind=video`。ハロプロ公式のチャンネル18個）: アメブロと同じく**保存しない**。Worker（`watcher/src/youtube.js`）が、選んだチャンネルの公式のRSS（`https://www.youtube.com/feeds/videos.xml?channel_id=UC…`。最新15件。公式のキャッシュは15分）を取りに行き、題名・リンク・公開時刻だけにして返す（説明は読まない）。同じRSSは Cloudflare のキャッシュで15分使い回す
  - 対象は、公式の一覧（https://helloproject.com/link/）にある17チャンネルと、JPRTube。`rss.py` の `YT_CHANNELS`。チャンネルのID（`UC…`）は、チャンネルのページの RSS の印から引いて、`state/youtube_channels.json` に残す（引くのは、一覧に足したときの1回だけ）。個人のチャンネルは対象にしていない
  - `cat`: `video`（動画）／`shorts`（Shorts）。リンクが `/shorts/` の形かどうかで見分ける。Shorts は全体の3割ほど
  - `embed=1`（YouTube だけ。ほかは400）: 公式の埋め込みプレーヤー（`youtube-nocookie.com/embed/{動画のID}`）を本文に入れる。動画そのものを持つのではなく、YouTube 公式のプレーヤーを呼ぶだけ。リーダーによっては、記事の中の埋め込みが表示されない（その場合、リンクから開く）。無ければ、これまでどおり題名だけ
  - 公式がRSSを出しているので、チャンネル全体を見たいだけなら、トップの一覧にある公式のRSSをそのまま使うのが確実。動画とShortsを分けたい・複数チャンネルをまとめたい・埋め込みたい、のときにこの画面を使う
  - 一部のチャンネルが読めなくても、読めた分は返す。全部読めなければ502
- 手元の確認: `PUBLIC_DIR=<rss.py の出力先> NODE_USE_ENV_PROXY=1 node watcher/test.mjs`（時計の確認は、公式サイトへ本物の HEAD をする）

## 動かす仕組み（Cloudflare の Cron Trigger が時計、GitHub Actions が作業）

GitHub Actions の定期実行は時刻を当てにできないので使わない。Cloudflare の Worker `hello-rss`（フィードを配る Worker と同じもの）の Cron Trigger が、決まった時刻に GitHub の作業を呼ぶ。作業は3つに分けて、それぞれ独立して動く。

| 作業 | ワークフロー | 呼ぶタイミング（Cloudflare の cron は UTC） |
|---|---|---|
| RSS | `rss.yml` | 公式が更新されたとき（15分おきに `/schedule/` の `Last-Modified` を見て、直近17分以内なら）と、毎朝 05:00 JST（今日の予定） |
| スケジュールの Discord 通知 | `hpwatch.yml` | 毎日 18:00 JST |
| プロフィール | `profiles.yml` | 毎日 18:00 JST |

- フィードに無いURLは、トップページ（一覧）へ302で移す（`watcher/src/index.js` の `fetch`。あるファイルは Cloudflare が直接返すので、ここには来ない）
- 時計のコードは `watcher/src/index.js`、cron の設定はルートの `wrangler.toml`。RSS の公開（`rss.yml` の `wrangler deploy`）のたびに、フィードと一緒に送り直す。時計の手順を変えたら、次の RSS の公開から効く
- Worker は記憶（KV など）を持たない。二重に呼んでも、どの作業も新しい分だけを処理するので困らない
- 呼び出しに失敗したら、そのたびに Worker が Discord に知らせる（呼び出しは1日に十数回しか無いので、続いても数は限られる）
- GitHub の Secrets は `CLOUDFLARE_API_TOKEN`・`CLOUDFLARE_ACCOUNT_ID`・`DISCORD_WEBHOOK_URL`（hpwatch と profiles 用）。Worker に入れる秘密の値は、Cloudflare のダッシュボードで直接入れる（hello-rss → Settings → Variables and Secrets → Secret）: `GH_TOKEN`（GitHub の fine-grained トークン。このリポジトリだけ・Actions の実行権限だけ。期限が切れると呼べなくなり、Discord に知らせが来る）と `DISCORD_WEBHOOK_URL`
- 手元の確認: `NODE_USE_ENV_PROXY=1 node watcher/test.mjs`（公式サイトへの HEAD は本物、GitHub と Discord は偽物）
- 公式の更新の記録: `rss.yml` が更新のたびに `state/observe.jsonl` へ1行（版の時刻・見つけた新着の件数）。取りに行く時間帯を決めるための記録
- GitHub の cron（`schedule`）は使わない（遅れる・飛ぶことがあるため）。定期の作業は、すべて Cloudflare の時計から `workflow_dispatch` で呼ぶ。以前あった `watch.yml`（スケジュールとプロフィールを1つにまとめた作業）と `rss.yml` の `schedule` は、2026-11-20 の 18:00 JST の呼び出し（hpwatch・profiles の成功と Discord への通知）を確かめて、削除した

## radio/

NAS で radiko のタイムフリーを自動で回収するラジオ録音。GitHub Actions は使わない。→ [radio/README.md](radio/README.md)

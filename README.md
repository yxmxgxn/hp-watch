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
- ファンクラブニュース: `fc/{all,news,event,concert,goods,stream,other}.xml`（全体・ニュース・公式のタブ5つ）と `fc/group/{グループ}.xml`（8グループ）
  - Hello! Project オフィシャルファンクラブ（`upfc.jp/helloproject`）のHTMLを読む（JSONは無い）。「すべて」の一覧（1ページ15件）の1ページ目を見て、新しく載った記事を足す。初回は2ページ分
  - ニュースは、公式のタブには無いが「すべて」の中にある分類。そこだけ別のフィードにしている
  - グループ別は、グループのページの一覧（`news_list.php?gid=…`、1ページ目）に載っているかどうかで決める（記事にはグループの印が無い）。グループの番号（gid）はグループのページの「view more」のリンクから読んで、状態に持つ
  - 更新を見分ける印（`Last-Modified` など）が無いので、`rss.yml` が動くたびに見る（ハロプロの公式の更新を検知したときと毎朝5:00）
  - 日付は公式の書き方（`2026.11.19`）のまま。記事の時刻は、お知らせと同じ決め方（初めて見た時刻、分・秒は0。初回は日付の正午）
  - 状態は `state/rss_fc.json`。取得に失敗しても、前回の状態があればそれでフィードを書く（他のフィードの公開を止めない）。1グループだけ失敗したときは、そのグループだけ飛ばす
- JPROOM は所属が個人なので、どれも全員まとめて1本
- `index.html`（一覧）と `all.opml`（まとめて登録）
- 手元で試す: `python rss.py --out public --state-dir /tmp/rss-state`（`state/` を汚さないように）

- 公開: `.github/workflows/rss.yml` が JST 5:07 と 8〜23時の3時間おきに `rss.py` を動かし、`wrangler.toml` で Cloudflare Workers（`hello-rss`）に送る
  - Secrets に `CLOUDFLARE_API_TOKEN`（テンプレート「Edit Cloudflare Workers」、アカウントと `karin-archive.com` に絞る）と `CLOUDFLARE_ACCOUNT_ID` が要る。無い間は公開を飛ばす
  - どちらかのサイトの取得に失敗した回は公開しない（そのサイトのフィードが消えるので）

これから: TVer の番組ページの対応表。

## 選んで作る RSS（`/custom/`）

好きな人・グループ・カテゴリだけのフィードを、各自が選んで作れる。選んだ内容はURLに入るので、保存先（KV など）は要らない。

- 選ぶ画面: `https://hello-rss.karin-archive.com/custom/`（トップの一覧からリンク）
- URL: `/custom.xml?site=hp&kind=news&who=angerme,juicejuice&cat=EVENT`
  - `site`（`hp`・`jproom`・`fc`）と `kind`（`news`・`schedule`）は必須。**サイトはまたがない**。**お知らせとスケジュールは別**（1本＝1つのサイト × 片方の種類）
  - `who`（人・グループ）、`cat`（カテゴリ）は任意。カンマ区切りで複数。同じ項目の中は「どれか」、`who` と `cat` は「両方」。空なら全部
  - 選べるのは `data/options.json` にある値だけ。それ以外は400を返す（誤ったURLを、空のフィードとして登録させない）。最新50件
- 仕組み: 公開のたびに `rss.py`（`selectable.py`）が `data/{site}-{kind}.json`（人・グループ・カテゴリ付きの記事、最大400件）と `data/options.json`（選べる値）を置く。Worker（`watcher/src/custom.js`）がデータを読んで、条件に合う記事だけを RSS にして返す（`Cache-Control: max-age=600`）
- 人・グループのキーは、公式のプロフィールの `slug`（無い・重複するものは `id{番号}`）。名前が空のプロフィールは選択肢に出さない
- スケジュールの元は「新しく載った出演」（MEDIA のフィードと同じ）。新しく載ったものから溜めるので、使い始めは記事が少なく、日がたつほど増える。人・グループは、記事がまだ無くても選べる
- 手元の確認: `PUBLIC_DIR=<rss.py の出力先> NODE_USE_ENV_PROXY=1 node watcher/test.mjs`（`PUBLIC_DIR` を付けると、選択式フィードのテストも動く）

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
- `watch.yml`（スケジュールとプロフィールを1つにまとめた古い作業）と、`rss.yml` の `schedule` は、上の仕組みが確かめられるまでの保険として残してある。確かめたら消す

## radio/

NAS で radiko のタイムフリーを自動で回収するラジオ録音。GitHub Actions は使わない。→ [radio/README.md](radio/README.md)

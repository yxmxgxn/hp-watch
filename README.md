# hp-watch

ハロー！プロジェクト公式サイトとJPROOMのスケジュールJSONを毎日取得し、前回からの差分をDiscordに通知する。

> GitHub Actions の定期実行は時刻を当てにできない。`watch.yml` は 15:00 JST の予定だが、実際は 21:30〜00:40 に動いている（2026-11-07〜18 の12回）。`rss.yml` も遅れたり飛ばされたりする。時刻が大事なものは Cloudflare の Cron Trigger（`watcher/`）に任せる。

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

公式のJSONから RSS を作る。公開先は `https://hello-rss.karin-archive.com`（Cloudflare Workers、未設定）。

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
- JPROOM は所属が個人なので、どれも全員まとめて1本
- `index.html`（一覧）と `all.opml`（まとめて登録）
- 手元で試す: `python rss.py --out public --state-dir /tmp/rss-state`（`state/` を汚さないように）

- 公開: `.github/workflows/rss.yml` が JST 5:07 と 8〜23時の3時間おきに `rss.py` を動かし、`wrangler.toml` で Cloudflare Workers（`hello-rss`）に送る
  - Secrets に `CLOUDFLARE_API_TOKEN`（テンプレート「Edit Cloudflare Workers」、アカウントと `karin-archive.com` に絞る）と `CLOUDFLARE_ACCOUNT_ID` が要る。無い間は公開を飛ばす
  - どちらかのサイトの取得に失敗した回は公開しない（そのサイトのフィードが消えるので）

これから: TVer の番組ページの対応表。

## watcher/（公式サイトの更新を記録する Worker・様子見用）

Cloudflare Workers の Cron Trigger で15分おきに、公式サイトの更新（版）を記録する。いつお知らせ・スケジュールが更新されるかを数日見て、取りに行く時間帯を決めるため。

- `/schedule/` の `Last-Modified`・`ETag`（HEADだけ）で更新を見分け、変わったときだけ版の名前（`2611191300-…` ＝ 日本時間 2026-11-19 13:00）と、お知らせ・スケジュールのJSONのサイズ／ETagを記録する。JSON本体は取らない
- 記録は KV。見るのは `https://hello-rss-watch.<サブドメイン>.workers.dev/<LOG_PATH>?text`（`LOG_PATH` は `watcher/wrangler.toml`。公式サイトの更新時刻しか見えない）
- 公開: `.github/workflows/watcher.yml`（`watcher/` を `main` に入れたとき、または手動）。Secrets は `rss.yml` と同じ。トークンに「Workers KV Storage: Edit」が要る
- 手元の確認: `NODE_USE_ENV_PROXY=1 node watcher/test.mjs`（本物の公式サイトに HEAD する。KV は偽物）

## radio/

NAS で radiko のタイムフリーを自動で回収するラジオ録音。GitHub Actions は使わない。→ [radio/README.md](radio/README.md)

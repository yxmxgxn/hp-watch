# radio（ラジオ録音）

hp-watch の GitHub Actions とは関係なく、NAS で動く。

NAS（DS220j）で radiko のタイムフリーを数時間おきに見て、番組リストの回で**まだ NAS に無いもの**を落とし、
`radio_rename.ps1` と同じ名前（`第{回}回 {YYYYMMDD}_{番組名}.m4a`）で NAS と BOX に置く。

- 番組リストは [`programs.json`](programs.json)。スマホの GitHub アプリで編集してコミットすれば、NAS の次の実行から効く
- 保存した回と異常は Discord に届く。NAS が止まったら healthchecks.io が知らせる

```
radio/programs.json  … 録る番組
radio/nas/           … NAS で動く録音コンテナ（Docker）
```

## 動き

- 放送時刻に動く必要が無いので、NAS の再起動やコンテナの停止があっても1週間以内に動けば取り戻す
- 回数は保存先フォルダの最大の回 +1（1本も無ければ `first_no`）。同じ日付の回があれば保存済み
- 保存先の最新回より古い回には触らない（自動にする前の回は対象外）
- ある回が取れないうちは、その番組の次の回へ進まない（番号がずれないように）
- 再放送（タイトルに「(再)」「【再】」「再放送」）は除く。それでも紛れるときは `start` で放送開始時刻を指定する
- 録音そのものは [rec_radiko_ts.sh](https://github.com/uru2/rec_radiko_ts) に任せる

雑談ラジオの Podcast（mp3）は対象外。

## programs.json の書き方

```json
{
  "name": "宮本佳林の雑談ラジオ",
  "station": "JORF",
  "dir": "Movie/Music/宮本佳林/宮本佳林の雑談ラジオ",
  "box": "RADIO/ラジオ日本/宮本佳林の雑談ラジオ",
  "first_no": 242
}
```

| 項目 | 必須 | 意味 |
|---|---|---|
| `name` | ○ | ファイル名に使う番組名。番組表のタイトルもこれで探す |
| `station` | ○ | radiko の局ID（TBS / QRR 文化放送 / LFR ニッポン放送 / JORF ラジオ日本 / FMT TOKYO FM / FMJ J-WAVE / INT interfm など） |
| `dir` | ○ | NAS の保存先。共有フォルダ `Yuta` からのパス |
| `box` | | BOX の保存先。無ければ BOX へは送らない |
| `first_no` | | 保存先に1本も無いときの回数。あれば続きの番号になる |
| `match` | | 番組表のタイトルが `name` と違うとき、タイトルに含まれる語 |
| `start` | | 放送開始 `"HH:MM"`（`"25:00"` も可）。再放送と区別したいときだけ |
| `enabled` | | `false` で一時停止 |

書き間違えて読めないときは、前回読めた内容で動いて Discord に知らせる。

## 通知

Discord:
- 保存したとき: ✅ ファイル名
- 放送から48時間たっても取れていない: ⚠️ 期限まであと何日か（その後は1日1回）
- 1週間たって取り逃した: ⚠️（次の回はその番号で保存されるので、必要なら手で直す）
- BOX に24時間送れていない / programs.json を読めない: ⚠️

healthchecks.io: NAS は実行のたびに Ping する。途絶えたら healthchecks.io が知らせる（Discord にも送れる）。

## 準備

### 1. GitHub のトークン

NAS がこのリポジトリの programs.json を読むためのもの。
GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
- Repository access: Only select repositories → `hp-watch`
- Permissions: Contents → Read-only
- 期限は長めに（切れたら Discord に「programs.json を読めず」と来る）

### 2. healthchecks.io（無料）

https://healthchecks.io でチェックを1つ作る。Period 6 hours / Grace 20 hours。
Integrations で Discord（か メール）をつなぐ。Discord の Webhook は hp-watch と同じものでよい。チェックの Ping URL を控える。

### 3. BOX の認証（PC で1回）

ブラウザでのログインが要るので PC で作って NAS に持っていく。

1. https://rclone.org/downloads/ から Windows 版を落として展開
2. `rclone config` → `n`（新規）→ 名前 `box` → 種類 `box` → あとは Enter で既定のまま → ブラウザで BOX にログイン
3. `rclone config file` で出る場所の `rclone.conf` を NAS の `radio/nas/` にコピー
4. 確かめるなら `rclone lsd box:RADIO`

### 4. NAS

```sh
cd /volume1/docker
git clone https://github.com/yxmxgxn/hp-watch.git   # git が無ければ zip を置く
cd hp-watch/radio/nas
cp .env.example .env        # GITHUB_TOKEN / DISCORD_WEBHOOK / HEALTHCHECK_URL を書く
# rclone.conf をここに置く
sudo docker compose up -d --build     # 古い DSM なら docker-compose up -d --build
sudo docker logs -f radio-recorder     # 動きを見る（Ctrl+C で抜ける。コンテナは止まらない）
```

起動するとすぐ1回回り、以降6時間おき。最初の1回で今週分が入れば成功。
rfriend は止めてよい。

## 困ったとき

- **全部「録音に失敗」になる** → radiko の仕様変更のことが多い。
  rec_radiko_ts のコミットが新しくなっていたら、`radio/nas/Dockerfile` の `REC_RADIKO_TS_COMMIT` を変えて
  `sudo docker compose up -d --build`
- **局IDが無いと言われる** → `station` を radiko の局IDで
- **他の地域の局を録りたい** → radiko プレミアムに入って `.env` の `RADIKO_MAIL` / `RADIKO_PASS` を書く
- **すぐ回したい** → `sudo docker restart radio-recorder`
- **コードを更新した** → NAS で `git pull && sudo docker compose up -d --build`

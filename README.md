# YoneRai Discord Bot

## 概要
YoneRai Discord Bot は、音楽再生、翻訳、AI 質問などの機能を備えた多目的 Bot です。`y!` や `y?` で始まるテキストコマンド、または Discord のスラッシュコマンドから操作できます。

## 主な機能

### 🎵 音楽
- **再生**: `y!play` / `/play` — キーワード検索・URL・音声ファイルをキューに追加。
  - キーワードの場合は YouTube 検索の最初の結果を自動で取得します。URL はプレイリストにも対応。
  - MP3/WAV/OGG などの音声ファイルを添付して再生することもでき、複数ファイルの同時添付も可能です。
  - `y!play` は添付ファイルを先に、テキスト部分はカンマ区切りで複数曲追加できます。
  - `/play` は `query` `file` 引数を指定した順にキューへ追加します。（`query` 内のカンマは分割しません）
- **キュー表示・操作**: `y!queue` / `/queue`
  - Skip / Shuffle / Loop / Pause / Resume / Leave などをボタンで操作。
- **曲削除**: `y!remove <番号>` / `/remove <番号>`
- **一括削除**: `y!keep <番号>` / `/keep <番号>` で指定番号以外の曲を削除。
- **シーク**: `y!seek <時間>` / `/seek <時間>` で指定位置から再生。
- **巻き戻し／早送り**:
  - `y!rewind <時間>` / `/rewind <時間>` … 現在の曲を指定秒数だけ巻き戻し
  - `y!forward <時間>` / `/forward <時間>` … 現在の曲を指定秒数だけ早送り
  - `<時間>` には `10`, `1m`, `1分30秒`, `1:20` など様々な表記が使えます
  - 例：`y!rewind 10`（10秒戻る）, `y!forward 1分`（1分進む）
- **退出**: `y!stop` / `/stop`

### 💬 翻訳
- メッセージに国旗リアクションを付けると自動で翻訳を返信。

### 🤖 AI / ツール
- **AI 質問**: `y? <質問>` / `/gpt <質問>` — GPT‑4.1 が Web 検索や Python 実行を用いて回答。

### 🎤 音声文字起こし
- `y!yomiage` / `/yomiage` — ボイスチャットの発言を OpenAI TTS で読み上げ。
- `y!mojiokosi` / `/mojiokosi` — 発言内容をテキストチャンネルへ自動送信。

### 🧑 ユーザー情報
- `y!user <@メンション|ID>` / `/user [ユーザー]` — 指定ユーザーの詳細情報をEmbedで表示します。
- `y!server` / `/server` — このサーバーの詳細情報をEmbedで表示します。

### 🕹️ その他
- `y!ping` / `/ping` — 応答速度表示。
- `y!say <text>` / `/say` — テキストを Bot が発言。
- `y!date [timestamp]` / `/date` — 日時を Discord 形式で表示。
- `y!XdY` / `/dice` — ダイスロール（例: `2d6`）。
- `y!purge <n|link>` / `/purge` — メッセージを一括削除。
- `y!help` / `/help` — コマンド一覧を表示。
- `y!?` — 返信で使用するとメッセージを名言カード化。

## 使い方

### テキストコマンド
メッセージの先頭に `y!` または `y?` を付けて送信します。

例:
```
y!play Never Gonna Give You Up
y? 猫とは？
```

### スラッシュコマンド
Discord の入力欄で `/` を入力し、コマンド名を選択します。

例:
```
/play
/queue
/remove 1 2 3
/gpt 今日は何の日？
```

## インストール
1. Python 3.10 以上を用意してください。
2. 依存ライブラリをインストールします。
   ```bash
   pip install -r requirements.txt
   ```
3. Discord の Bot トークンを `token.txt` に、OpenAI API キーを `OPENAIKEY.txt` に保存します。
4. 音楽再生には `ffmpeg` が必要です。システムにインストールしてから下記を実行してください。
   ```bash
   python DiscordYONE.py
   ```

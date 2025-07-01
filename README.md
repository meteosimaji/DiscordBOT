# YoneRai Discord Bot

## 概要
YoneRai Discord Bot は音楽再生、翻訳、AI 質問に加え、ボイスチャンネルでの読み上げ (TTS) と文字起こし (STT) を行う多機能 Bot です。`y!` や `y?` で始まるテキストコマンド、または Discord のスラッシュコマンドから操作できます。

## 主な機能
### 🎵 音楽
- **再生**: `y!play` / `/play` — キーワード検索・URL・音声ファイルをキューへ追加。
- **キュー操作**: `/queue` で Skip / Shuffle / Loop などをボタンから実行。
- **シーク/早送り/巻き戻し** など主要なプレイヤー機能をサポート。

### 🎤 ボイス機能
- **読み上げ `/yomiage`** — テキストチャンネルの発言を gTTS で音声化し、VC で再生します。
- **文字起こし `/mojiokosi`** — VC の発言を Whisper で認識し、テキストチャンネルへ送信します。
  - 音声受信には `discord-ext-voice-recv` を使用しています。
  - Whisper (faster-whisper) はローカルで動作するため API キーは不要です。

### 🤖 AI / ツール
- **AI 質問**: `y? <質問>` / `/gpt <質問>` — GPT‑4.1 へ質問できます。

### その他
翻訳リアクション、ユーザー情報表示、ダイスロールなど多数のユーティリティを備えています。

## インストール
1. Python 3.10 以上を用意してください。
2. 依存ライブラリをインストールします。
   ```bash
   pip install -r requirements.txt
   ```
3. `.env.example` を `.env` にコピーし、自身の `DISCORD_TOKEN` と `OPENAI_API_KEY` を設定します。
4. `ffmpeg` をインストールした上で以下を実行します。
   ```bash
   python DiscordYONE.py
   ```

ボイス機能を利用する際は、Bot を VC に参加させてから `/yomiage` や `/mojiokosi` を実行してください。

# YoneRai Discord Bot
概要
YoneRai Discord Botは、音楽再生・AI質問・翻訳・各種便利コマンドを搭載した多機能BOTです。
「/コマンド」または「y!コマンド」で直感的に使えるよう設計されています。

主な機能
🎵 音楽機能
再生:
y!play / /play … 曲やプレイリストを追加
/playはfile引数で音楽ファイルも添付OK

キュー管理:
y!queue / /queue … キューの表示・操作
y!remove / /remove … 指定曲の削除
y!keep / /keep … 指定番号以外の一括削除
y!stop / /stop … VC退出

💬 翻訳機能
メッセージに国旗リアクションを付けるだけで自動翻訳

🤖 AI/ツール
AI質問:
y? <質問> / /gpt <質問> … ChatGPT（GPT-4.1）がWeb検索・Python実行で答えます

🧑 ユーザー情報
y!user <id> / /user <id> … プロフィール表示

🕹️ その他
y!ping / /ping … 応答速度

y!say <text> / /say … エコー

y!date / /date … 日時表示（/dateはtimestampオプションもOK）

y!XdY / /dice … ダイス（例: 2d6）

y!purge <n|link> / /purge … メッセージ一括削除

y!help / /help … このヘルプ

y!? … 返信で使うと名言化

使い方
テキストコマンド
メッセージの先頭に y! や y? を付けて送信

例:
y!play Never Gonna Give You Up
y? 猫とは？

スラッシュコマンド
Discordの入力欄で / を入力し、コマンドを選択

例:
/play
/queue
/remove 1 2 3
/gpt 今日は何の日？

インストール・導入
Python 3.10以上 推奨

必要なライブラリをインストール

nginx
コピーする
編集する
pip install -r requirements.txt
DiscordのBotトークン・OpenAIキーを用意してtoken.txt・OPENAIKEY.txtに保存

DiscordYONE.pyを実行

nginx
コピーする
編集する
python DiscordYONE.py

import os, re, time, random, discord, openai,tempfile

# ───────────────── TOKEN / KEY ─────────────────
with open("token.txt", "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

with open("OPENAIKEY.txt", "r", encoding="utf-8") as f:
    openai.api_key = f.read().strip()

# ───────────────── Discord 初期化 ─────────────────
intents = discord.Intents.default()
intents.message_content = True          # メッセージ内容を取得
intents.reactions = True 
intents.members   = True        # 追加
intents.presences = True 
intents.voice_states    = True 
client = discord.Client(intents=intents)

# ───────────────── 便利関数 ─────────────────
def parse_cmd(content: str):
    """
    y!cmd / y? 解析。戻り値 (cmd, arg) or (None, None)
    """
    if content.startswith("y?"):
        return "gpt", content[2:].strip()
    if not content.startswith("y!"):
        return None, None
    body = content[2:].strip()

    # Dice 記法 (例 3d6, d20, 1d100)
    if re.fullmatch(r"\d*d\d+", body, re.I):
        return "dice", body

    parts = body.split(maxsplit=1)
    return parts[0].lower(), parts[1] if len(parts) > 1 else ""

from yt_dlp import YoutubeDL
YTDL_OPTS = {
    "quiet": True,
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "default_search": "ytsearch",
    "noplaylist": True,
}

def yt_extract(url_or_term: str) -> tuple[str, str]:
    """(title, direct_audio_url) を返す"""
    with YoutubeDL(YTDL_OPTS) as ydl:
        info = ydl.extract_info(url_or_term, download=False)
        # ytsearch の場合は 'entries' にリストされる
        if "entries" in info:
            info = info["entries"][0]
        return info["title"], info["url"]
    
import asyncio, collections

class MusicState:
    def __init__(self):
        self.queue   = collections.deque()
        self.loop    = False
        self.current = None
        self.play_next = asyncio.Event()
        self.queue_msg: discord.Message | None = None   # ← 追加

    async def player_loop(self, voice: discord.VoiceClient, channel: discord.TextChannel):
        """
        キューが続く限り再生し続けるループ。
        self.current に再生中タプル (title,url) をセットし、
        曲が変わるたびに refresh_queue() を呼んで Embed を更新。
        """
        while True:
            self.play_next.clear()

            # キューが空なら 5 秒待機→まだ空なら切断
            if not self.queue:
                await asyncio.sleep(5)
                if not self.queue:
                    await voice.disconnect()
                    self.queue_msg = None
                    return

            # 再生準備
            self.current = self.queue[0]
            title, url   = self.current

            ffmpeg_audio = discord.FFmpegPCMAudio(
                source=url,
                executable="ffmpeg",
                before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                options='-vn -loglevel warning -af "volume=0.9"'
            )
            voice.play(ffmpeg_audio, after=lambda _: self.play_next.set())

            # チャット通知 & Embed 更新
            await channel.send(f"▶️ **Now playing**: {title}")
            await refresh_queue(self)

            # 次曲まで待機
            await self.play_next.wait()

            # ループOFFなら再生し終えた曲をキューから外す
            if not self.loop and self.queue:
                self.queue.popleft()

# クラス外でOK
async def refresh_queue(state: "MusicState"):
    """既存のキュー Embed を最新内容に書き換える"""
    if state.queue_msg:               # ← これだけで十分
        try:
            await state.queue_msg.edit(embed=make_embed(state))
        except discord.HTTPException:
            pass

# ──────────── 🖼 名言化 APIヘルパ ────────────
import json, aiohttp, pathlib

FAKEQUOTE_URL = "https://api.voids.top/fakequote"
SAVE_NAME     = "YoneRAIMEIGEN.jpg"

async def make_quote_image(user, text, color=False) -> pathlib.Path:
    """FakeQuote API で名言カードを生成しローカル保存 → Path を返す"""
    payload = {
        "username"    : user.name,
        "display_name": user.display_name,
        "text"        : text[:200],
        "avatar"      : user.display_avatar.url,
        "color"       : color,
    }

    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            FAKEQUOTE_URL,
            json=payload,
            headers={"Accept": "text/plain"},
            timeout=10
        ) as r:
            # 200, 201 どちらも成功扱いにする
            raw = await r.text()
            # Content-Type が text/plain でも JSON が来るので自前でパースを試みる
            try:
                data = json.loads(raw)
                if not data.get("success", True):
                    raise RuntimeError(data)
                img_url = data["url"]
            except json.JSONDecodeError:
                # プレーンで URL だけ返ってきた場合
                img_url = raw.strip()

        async with sess.get(img_url) as img:
            img_bytes = await img.read()

    path = pathlib.Path(SAVE_NAME)
    path.write_bytes(img_bytes)
    return path

# ──────────── ボタン付き View ────────────
class QuoteView(discord.ui.View):
    def __init__(self, invoker: discord.User, payload: dict):
        super().__init__(timeout=180)
        self.invoker = invoker    # 操作できる人
        self.payload = payload    # {user, text, color}

    # ── 作った人だけ操作可能 ──
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message(
                "作った人しか触れないよ！", ephemeral=True
            )
            return False
        return True

    async def _regen(self, interaction: discord.Interaction):
        path = await make_quote_image(**self.payload)
        await interaction.response.edit_message(
            attachments=[discord.File(path, filename=path.name)],
            view=self
        )

    @discord.ui.button(label="🎨 カラー", style=discord.ButtonStyle.success)
    async def btn_color(self, inter: discord.Interaction, _):
        self.payload["color"] = True
        await self._regen(inter)

    @discord.ui.button(label="⚫ モノクロ", style=discord.ButtonStyle.secondary)
    async def btn_mono(self, inter: discord.Interaction, _):
        self.payload["color"] = False
        await self._regen(inter)

# ──────────── 🎵  VCユーティリティ ────────────
guild_states: dict[int, "MusicState"] = {}

async def ensure_voice(msg: discord.Message) -> discord.VoiceClient | None:
    """発話者が入っている VC へ Bot を接続（既に接続済みならそれを返す）"""
    if msg.author.voice is None or msg.author.voice.channel is None:
        await msg.reply("🎤 まず VC に入室してからコマンドを実行してね！")
        return None

    voice = msg.guild.voice_client
    if voice and voice.is_connected():                 # すでに接続済み
        if voice.channel != msg.author.voice.channel:  # 別チャンネルなら移動
            await voice.move_to(msg.author.voice.channel)
        return voice

    # 未接続 → 接続を試みる（10 秒タイムアウト）
    try:
        return await asyncio.wait_for(
            msg.author.voice.channel.connect(self_deaf=True),
            timeout=10
        )
    except asyncio.TimeoutError:
        await msg.reply("⚠️ VC への接続に失敗しました。もう一度試してね！")
        return None

# ──────────── 🎵  Queue UI ここから ────────────
def make_embed(state: "MusicState") -> discord.Embed:
    emb = discord.Embed(title="🎶 Queue")

    # Now Playing
    if state.current:
        title, _ = state.current
        emb.add_field(name="Now Playing", value=title, inline=False)
    else:
        emb.add_field(name="Now Playing", value="Nothing", inline=False)

    # Up Next
    queue_list = list(state.queue)
    if state.current in queue_list:   # どこにあっても 1 回だけ除外
        queue_list.remove(state.current)

    if queue_list:
        lines, chars = [], 0
        for i, (t, _) in enumerate(queue_list):
            line = f"{i+1}. {t}"
            if chars + len(line) + 1 > 800:        # 800 文字で打ち止め
                lines.append(f"…and **{len(queue_list)-i}** more")
                break
            lines.append(line)
            chars += len(line) + 1
        body = "\n".join(lines)
    else:
        body = "Empty"

    emb.add_field(name="Up Next", value=body, inline=False)
    emb.set_footer(text=f"Loop: {'ON' if state.loop else 'OFF'}")
    return emb

class ControlView(discord.ui.View):
    """Skip / Shuffle / Pause / Resume / Loop をまとめた操作ボタン"""
    def __init__(self, state: "MusicState", vc: discord.VoiceClient, owner_id: int):
        super().__init__(timeout=180)
        self.state, self.vc, self.owner_id = state, vc, owner_id

    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.owner_id:
            await itx.response.send_message("🙅 発行者専用ボタンだよ", ephemeral=True)
            return False
        return True

    # --- ボタン定義 ---
    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.primary)
    async def _skip(self, itx: discord.Interaction, _: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.stop()
        await itx.response.defer()

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.primary)
    async def _shuffle(self, itx: discord.Interaction, _: discord.ui.Button):
        random.shuffle(self.state.queue)
        await refresh_queue(self.state)
        await itx.response.defer()

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.secondary)
    async def _pause_resume(self, itx: discord.Interaction, _: discord.ui.Button):
        if self.vc.is_playing():
            self.vc.pause()
        elif self.vc.is_paused():
            self.vc.resume()
        await itx.response.defer()

    @discord.ui.button(label="🔂 Loop ON/OFF", style=discord.ButtonStyle.success)
    async def _loop_toggle(self, itx: discord.Interaction, _: discord.ui.Button):
        self.state.loop = not self.state.loop
        await refresh_queue(self.state)
        await itx.response.defer()

# ──────────── 🎵  Queue UI ここまで ──────────

# ───────────────── コマンド実装 ─────────────────
async def cmd_ping(msg: discord.Message):
    ms = client.latency * 1000
    await msg.channel.send(f"Pong! `{ms:.0f} ms` 🏓")

async def cmd_queue(msg: discord.Message, _):
    state = guild_states.get(msg.guild.id)
    if not state:
        await msg.reply("キューは空だよ！"); return
    vc   = msg.guild.voice_client
    view = ControlView(state, vc, msg.author.id)
    state.queue_msg = await msg.channel.send(embed=make_embed(state), view=view)


async def cmd_say(msg: discord.Message, text: str):
    if not text.strip():
        await msg.channel.send("何を言えばいい？")
        return
    if len(text) <= 2000:
        await msg.channel.send(text)
    else:
        await msg.channel.send(file=discord.File(fp=text.encode(), filename="say.txt"))

async def cmd_date(msg: discord.Message, arg: str):
    ts = int(arg) if arg.isdecimal() else int(time.time())
    await msg.channel.send(f"<t:{ts}:F>")              # 例：2025年6月28日 土曜日 15:30

async def cmd_user(msg: discord.Message, arg: str = ""):
    """
    y!user            … 呼び出し主
    y!user @mention   … そのメンション先
    y!user <ID>       … ユーザー ID 直指定
    """
    arg = arg.strip()
    target: discord.User | discord.Member

    # ---------- 対象ユーザーを決める ----------
    if not arg:                                  # 引数なし → 自分
        target = msg.author

    elif arg.isdigit():                          # ユーザー ID
        try:
            target = await client.fetch_user(int(arg))
        except discord.NotFound:
            await msg.reply("その ID のユーザーは見つかりませんでした。")
            return

    elif arg.startswith("<@") and arg.endswith(">"):  # メンション
        uid = arg.removeprefix("<@").removeprefix("!").removesuffix(">")
        try:
            target = await client.fetch_user(int(uid))
        except discord.NotFound:
            await msg.reply("そのユーザーは見つかりませんでした。")
            return
    else:
        await msg.reply("`y!user` / `y!user @メンション` / `y!user 1234567890` の形式で指定してね！")
        return

    # ---------- Guild 参加情報が取れるか ----------
    member: discord.Member | None = None
    if msg.guild:
        # キャッシュをまず見る
        member = msg.guild.get_member(target.id)
        # キャッシュに無ければ API で取得（権限があれば）
        if member is None:
            try:
                member = await msg.guild.fetch_member(target.id)
            except discord.NotFound:
                member = None   # DM 専用ユーザーなど

    # ---------- Embed 生成 ----------
    embed = discord.Embed(title="ユーザー情報", colour=0x2ecc71)
    embed.set_thumbnail(url=target.display_avatar.url)

    # 基本
    embed.add_field(name="表示名", value=target.display_name, inline=False)
    embed.add_field(name="名前", value=f"{target} (ID: `{target.id}`)", inline=False)
    embed.add_field(name="BOTかどうか", value="✅" if target.bot else "❌")

    # アカウント作成
    embed.add_field(
        name="アカウント作成日",
        value=f"<t:{int(target.created_at.timestamp())}:F>",
        inline=False
    )

    # ------ サーバー固有情報 ------
    if member:
        if member.joined_at:
            embed.add_field(
                name="サーバー参加日",
                value=f"<t:{int(member.joined_at.timestamp())}:F>",
                inline=False
            )

        # ステータス（Presence Intent が ON になっている必要あり）
        status_map = {
            discord.Status.online: "オンライン",
            discord.Status.idle:   "退席中",
            discord.Status.dnd:    "取り込み中",
            discord.Status.offline:"オフライン / 非表示"
        }
        embed.add_field(
            name="ステータス",
            value=status_map.get(member.status, str(member.status)),
            inline=True
        )

        # ロール
        roles = [r for r in member.roles if r.name != "@everyone"]
        if roles:
            embed.add_field(name="ロール数", value=str(len(roles)), inline=True)
            embed.add_field(name="最高ロール", value=roles[-1].mention, inline=True)

        # Boost
        if member.premium_since:
            embed.add_field(
                name="サーバーブースト中",
                value=f"<t:{int(member.premium_since.timestamp())}:R>",
                inline=True
            )

    await msg.channel.send(embed=embed)

async def cmd_dice(msg: discord.Message, nota: str):
    m = re.fullmatch(r"(\d*)d(\d+)", nota, re.I)
    if not m:
        await msg.channel.send("書式は `XdY` だよ（例 2d6, d20, 1d100）")
        return
    cnt = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    if not (1 <= cnt <= 10):
        await msg.channel.send("ダイスは 1〜10 個まで！"); return
    rolls = [random.randint(1, sides) for _ in range(cnt)]
    total = sum(rolls)
    txt = ", ".join(map(str, rolls))

    class Reroll(discord.ui.View):
        @discord.ui.button(label="🎲もう一回振る", style=discord.ButtonStyle.primary)
        async def reroll(self, inter: discord.Interaction, btn: discord.ui.Button):
            if inter.user.id != msg.author.id:
                await inter.response.send_message("実行者専用ボタンだよ！", ephemeral=True); return
            new = [random.randint(1, sides) for _ in range(cnt)]
            await inter.response.edit_message(
                content=f"🎲 {nota} → {', '.join(map(str,new))} 【合計 {sum(new)}】",
                view=self
            )
    await msg.channel.send(f"🎲 {nota} → {txt} 【合計 {total}】", view=Reroll())

import asyncio

async def cmd_gpt(msg: discord.Message, prompt: str):
    if not prompt:
        await msg.channel.send("`y?` の後に質問を書いてね！"); return
    await msg.channel.typing()
    try:
        # OpenAIリクエストを別スレッドで
        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(
            None,
            lambda: openai.responses.create(
                model="gpt-4.1",
                tools=[
                    {"type": "web_search_preview"},
                    {"type": "code_interpreter", "container": {"type": "auto"}}
                ],
                input=prompt,
                temperature=0.7
            )
        )
        ans = resp.output_text.strip()
        await msg.channel.send(ans[:1900] + ("…" if len(ans) > 1900 else ""))
    except Exception as e:
        await msg.channel.send(f"エラー: {e}")

# ──────────── 🎵  コマンド郡 ────────────
async def cmd_play(msg: discord.Message, query: str):
    """曲をキューに追加して再生を開始"""
    if not query:
        await msg.reply("`y!play <URL または 検索語>` の形式で使ってね！")
        return

    voice = await ensure_voice(msg)
    if not voice:
        return

    state = guild_states.setdefault(msg.guild.id, MusicState())

    # YouTube-DL/yt-dlp 等で URL 抽出
    try:
        title, url = yt_extract(query)
    except Exception as e:
        await msg.reply(f"🔍 取得失敗: {e}")
        return
    
    state.queue.append((title, url))
    await refresh_queue(state)          # ← 追加
    await msg.channel.send(f"⏱️ **Queued**: {title}")

    # 再生していなければループを起動
    if not voice.is_playing() and not state.play_next.is_set():
        client.loop.create_task(state.player_loop(voice, msg.channel))


async def cmd_stop(msg: discord.Message, _):
    """Bot を VC から切断し、キュー初期化"""
    if vc := msg.guild.voice_client:
        await vc.disconnect()
    guild_states.pop(msg.guild.id, None)
    await msg.add_reaction("⏹️")


# ──────────── 🎵  自動切断ハンドラ ────────────
@client.event
async def on_voice_state_update(member, before, after):
    """誰かが VC から抜けた時 ― Bot だけ残ったら自動切断"""
    if member.guild.id not in guild_states:
        return

    voice: discord.VoiceClient | None = member.guild.voice_client
    if not voice or not voice.is_connected():
        return

    # VC 内のヒト(≠bot) が 0 人になった？
    if len([m for m in voice.channel.members if not m.bot]) == 0:
        try:
            await voice.disconnect()
        finally:
            guild_states.pop(member.guild.id, None)

async def cmd_help(msg: discord.Message):
    await msg.channel.send(
        "**🎵 音楽機能**\n"
        "`y!play <URL/キーワード>` - 曲をキューに追加して再生\n"
        "`y!queue` - キュー表示＆ボタン操作（Skip / Shuffle / Pause / Resume / Loop）\n"
        "\n"
        "**💬 翻訳機能**\n"
        "国旗リアクションを付けると、そのメッセージを自動翻訳\n"
        "\n"
        "**🤖 AI/ツール**\n"
        "`y? <質問>` - ChatGPT-4.1 (Web検索 & コード実行対応)\n"
        "\n"
        "**🧑 ユーザー情報**\n"
        "`y!user <userid>` - プロフィールを表示\n"
        "\n"
        "**🕹️ その他**\n"
        "`y!ping` - 応答速度\n"
        "`y!say <text>` - エコー\n"
        "`y!date` - 今日の日時\n"
        "`y!XdY` - ダイス(例: y!2d6)\n"
        "`y!help` - このヘルプ\n"
        "`y!?`  - 返信で使うと名言化"
    )


# ───────────────── イベント ─────────────────
from discord import Activity, ActivityType, Status

# 起動時に 1 回設定
@client.event
async def on_ready():
    await client.change_presence(
        status=Status.online,                        # ← オンライン表示
        activity=Activity(type=ActivityType.playing,
                          name="y!help で使い方を見る")
    )
    print("LOGIN:", client.user)

# ------------ 翻訳リアクション機能ここから ------------

# flags.txt を読み込み「絵文字 ➜ ISO 国コード」を作る
SPECIAL_EMOJI_ISO: dict[str, str] = {}
with open("flags.txt", "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            emoji = parts[0]                  # 例 🇯🇵
            shortcode = parts[1]              # 例 :flag_jp:
            if shortcode.startswith(":flag_") and shortcode.endswith(":"):
                iso = shortcode[6:-1].upper() # jp -> JP
                SPECIAL_EMOJI_ISO[emoji] = iso

ISO_TO_LANG = {
    # A
    "AW": "Dutch",
    "AF": "Dari Persian",
    "AO": "Portuguese",
    "AI": "English",
    "AX": "Swedish",
    "AL": "Albanian",
    "AD": "Catalan",
    "AE": "Arabic",
    "AR": "Spanish",
    "AM": "Armenian",
    "AS": "Samoan",
    "AQ": "English",
    "TF": "French",
    "AG": "English",
    "AU": "English",
    "AT": "German",
    "AZ": "Azerbaijani",
    # B
    "BI": "Kirundi",
    "BE": "French",        # (also Dutch, German)
    "BJ": "French",
    "BQ": "Dutch",
    "BF": "French",
    "BD": "Bengali",
    "BG": "Bulgarian",
    "BH": "Arabic",
    "BS": "English",
    "BA": "Bosnian",
    "BL": "French",
    "BY": "Belarusian",
    "BZ": "English",
    "BM": "English",
    "BO": "Spanish",
    "BR": "Portuguese",
    "BB": "English",
    "BN": "Malay",
    "BT": "Dzongkha",
    "BV": "Norwegian",
    "BW": "English",
    # C
    "CF": "French",
    "CA": "English",
    "CC": "English",
    "CH": "German",        # (also French, Italian, Romansh)
    "CL": "Spanish",
    "CN": "Chinese (Simplified)",
    "CI": "French",
    "CM": "French",
    "CD": "French",
    "CG": "French",
    "CK": "English",
    "CO": "Spanish",
    "KM": "Comorian",
    "CV": "Portuguese",
    "CR": "Spanish",
    "CU": "Spanish",
    "CW": "Dutch",
    "CX": "English",
    "KY": "English",
    "CY": "Greek",         # (also Turkish)
    "CZ": "Czech",
    # D
    "DE": "German",
    "DJ": "French",
    "DM": "English",
    "DK": "Danish",
    "DO": "Spanish",
    "DZ": "Arabic",
    # E
    "EC": "Spanish",
    "EG": "Arabic",
    "ER": "Tigrinya",
    "EH": "Arabic",
    "ES": "Spanish",
    "EE": "Estonian",
    "ET": "Amharic",
    # F
    "FI": "Finnish",
    "FJ": "English",
    "FK": "English",
    "FR": "French",
    "FO": "Faroese",
    "FM": "English",
    # G
    "GA": "French",
    "GB": "English",
    "GE": "Georgian",
    "GG": "English",
    "GH": "English",
    "GI": "English",
    "GN": "French",
    "GP": "French",
    "GM": "English",
    "GW": "Portuguese",
    "GQ": "Spanish",
    "GR": "Greek",
    "GD": "English",
    "GL": "Greenlandic",
    "GT": "Spanish",
    "GF": "French",
    "GU": "English",
    "GY": "English",
    # H
    "HK": "Chinese (Traditional)",
    "HM": "English",
    "HN": "Spanish",
    "HR": "Croatian",
    "HT": "Haitian Creole",
    "HU": "Hungarian",
    # I
    "ID": "Indonesian",
    "IM": "English",
    "IN": "Hindi",
    "IO": "English",
    "IE": "English",
    "IR": "Persian",
    "IQ": "Arabic",
    "IS": "Icelandic",
    "IL": "Hebrew",
    "IT": "Italian",
    # J
    "JM": "English",
    "JE": "English",
    "JO": "Arabic",
    "JP": "Japanese",
    # K
    "KZ": "Kazakh",
    "KE": "Swahili",
    "KG": "Kyrgyz",
    "KH": "Khmer",
    "KI": "English",
    "KN": "English",
    "KR": "Korean",
    "KW": "Arabic",
    # L
    "LA": "Lao",
    "LB": "Arabic",
    "LR": "English",
    "LY": "Arabic",
    "LC": "English",
    "LI": "German",
    "LK": "Sinhala",
    "LS": "Sesotho",
    "LT": "Lithuanian",
    "LU": "Luxembourgish",
    "LV": "Latvian",
    # M
    "MO": "Chinese (Traditional)",
    "MF": "French",
    "MA": "Arabic",
    "MC": "French",
    "MD": "Romanian",
    "MG": "Malagasy",
    "MV": "Dhivehi",
    "MX": "Spanish",
    "MH": "Marshallese",
    "MK": "Macedonian",
    "ML": "French",
    "MT": "Maltese",
    "MM": "Burmese",
    "ME": "Montenegrin",
    "MN": "Mongolian",
    "MP": "English",
    "MZ": "Portuguese",
    "MR": "Arabic",
    "MS": "English",
    "MQ": "French",
    "MU": "English",
    "MW": "English",
    "MY": "Malay",
    "YT": "French",
    # N
    "NA": "English",
    "NC": "French",
    "NE": "French",
    "NF": "English",
    "NG": "English",
    "NI": "Spanish",
    "NU": "English",
    "NL": "Dutch",
    "NO": "Norwegian",
    "NP": "Nepali",
    "NR": "Nauruan",
    "NZ": "English",
    # O
    "OM": "Arabic",
    # P
    "PK": "Urdu",
    "PA": "Spanish",
    "PN": "English",
    "PE": "Spanish",
    "PH": "Filipino",
    "PW": "Palauan",
    "PG": "Tok Pisin",
    "PL": "Polish",
    "PR": "Spanish",
    "KP": "Korean",
    "PT": "Portuguese",
    "PY": "Spanish",
    "PS": "Arabic",
    "PF": "French",
    # Q
    "QA": "Arabic",
    # R
    "RE": "French",
    "RO": "Romanian",
    "RU": "Russian",
    "RW": "Kinyarwanda",
    # S
    "SA": "Arabic",
    "SD": "Arabic",
    "SN": "French",
    "SG": "English",
    "GS": "English",
    "SH": "English",
    "SJ": "Norwegian",
    "SB": "English",
    "SL": "English",
    "SV": "Spanish",
    "SM": "Italian",
    "SO": "Somali",
    "PM": "French",
    "RS": "Serbian",
    "SS": "English",
    "ST": "Portuguese",
    "SR": "Dutch",
    "SK": "Slovak",
    "SI": "Slovene",
    "SE": "Swedish",
    "SZ": "English",
    "SX": "Dutch",
    "SC": "English",
    "SY": "Arabic",
    # T
    "TC": "English",
    "TD": "French",
    "TG": "French",
    "TH": "Thai",
    "TJ": "Tajik",
    "TK": "Tokelauan",
    "TM": "Turkmen",
    "TL": "Tetum",
    "TO": "Tongan",
    "TT": "English",
    "TN": "Arabic",
    "TR": "Turkish",
    "TV": "Tuvaluan",
    "TW": "Chinese (Traditional)",
    "TZ": "Swahili",
    # U
    "UG": "English",
    "UA": "Ukrainian",
    "UM": "English",
    "UY": "Spanish",
    "US": "English",
    "UZ": "Uzbek",
    # V
    "VA": "Italian",
    "VC": "English",
    "VE": "Spanish",
    "VG": "English",
    "VI": "English",
    "VN": "Vietnamese",
    "VU": "Bislama",
    # W
    "WF": "French",
    "WS": "Samoan",
    # Y
    "YE": "Arabic",
    # Z
    "ZA": "English",
    "ZM": "English",
    "ZW": "English",
}


def flag_to_iso(emoji: str) -> str | None:
    """絵文字2文字なら regional-indicator → ISO に変換"""
    if len(emoji) != 2:
        return None
    base = 0x1F1E6
    try:
        return ''.join(chr(ord(c) - base + 65) for c in emoji)
    except:
        return None

@client.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    """メッセージに付いた国旗リアクションで自動翻訳"""

    # 1. BOT 自身のリアクションは無視
    if payload.member and payload.member.bot:
        return

    emoji = str(payload.emoji)

    # 2. 国旗 ⇒ ISO2 文字
    iso = SPECIAL_EMOJI_ISO.get(emoji) or flag_to_iso(emoji)
    if not iso:
        return

    # 3. ISO ⇒ 使用する言語名（例: "English"）
    lang = ISO_TO_LANG.get(iso)
    if not lang:
        print(f"[DEBUG] 未登録 ISO: {iso}")
        return

    # 4. 元メッセージ取得
    channel  = await client.fetch_channel(payload.channel_id)
    message  = await channel.fetch_message(payload.message_id)
    original = message.content.strip()
    if not original:
        return

    # 5. GPT-4.1 で翻訳
    async with channel.typing():
        try:
            resp = openai.chat.completions.create(
                model="gpt-4.1",
                messages=[
                    {
                        "role": "system",
                        "content": (
                            f"Translate the following text into {lang}. "
                            f"The flag emoji is {emoji}. Return only the translation."
                        )
                    },
                    {"role": "user", "content": f"{emoji} {original}"}
                ],
                max_tokens=1000,
                temperature=0.3,
            )
            translated = resp.choices[0].message.content.strip()

            # 6. Discord 2000 文字制限に合わせて 1 通で送信
            header     = f"💬 **{lang}** translation:\n"
            available  = 2000 - len(header)
            if len(translated) > available:
                # ヘッダーを含めて 2000 文字ちょうどになるように丸める
                translated = translated[:available - 3] + "..."

            await channel.send(header + translated)

        except Exception as e:
            # 失敗したらメッセージ主へリプライ（失敗した場合はチャンネルに通知）
            try:
                await message.reply(f"翻訳エラー: {e}")
            except:
                await channel.send(f"翻訳エラー: {e}")
            print("[ERROR] 翻訳失敗:", e)


@client.event
async def on_message(msg: discord.Message):
    # ① Bot の発言は無視
    if msg.author.bot:
        return

    # ② y!? で名言カード化
    if msg.content.strip().lower() == "y!?" and msg.reference:
        try:
            # 返信元メッセージ取得
            src = await msg.channel.fetch_message(msg.reference.message_id)
            if not src.content:          # 空メッセージはスキップ
                return

            # 画像生成（初期はモノクロ）
            img_path = await make_quote_image(src.author, src.content, color=False)

            # ボタン用ペイロード
            payload = {
                "user":  src.author,
                "text":  src.content[:200],
                "color": False
            }
            view = QuoteView(invoker=msg.author, payload=payload)

            # 元メッセージへ画像リプライ
            await src.reply(
                content=f"🖼️ made by {msg.author.mention}",
                file=discord.File(img_path, filename=img_path.name),
                view=view
            )

            # y!? コマンドを削除
            await msg.delete()

        except Exception as e:
            await msg.reply(f"名言化に失敗: {e}", delete_after=10)
        return  # ← ここで終了し、既存コマンド解析へ進まない

    # ③ 既存コマンド解析
    cmd, arg = parse_cmd(msg.content)
    if cmd == "ping":   await cmd_ping(msg)
    elif cmd == "say":  await cmd_say(msg, arg)
    elif cmd == "date": await cmd_date(msg, arg)
    elif cmd == "user": await cmd_user(msg, arg)
    elif cmd == "dice": await cmd_dice(msg, arg or "1d100")
    elif cmd == "gpt":  await cmd_gpt(msg, arg)
    elif cmd == "help": await cmd_help(msg)
    elif cmd == "play": await cmd_play(msg, arg)
    elif cmd == "queue":await cmd_queue(msg, arg)

# ───────────────── 起動 ─────────────────
client.run(TOKEN)

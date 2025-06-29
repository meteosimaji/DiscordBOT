import os, re, time, random, discord, tempfile, logging
from discord import app_commands
from openai import OpenAI
from urllib.parse import urlparse, parse_qs
from logging.handlers import RotatingFileHandler

from dataclasses import dataclass

# ───────────────── TOKEN / KEY ─────────────────
with open("token.txt", "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

with open("OPENAIKEY.txt", "r", encoding="utf-8") as f:
    OPENAI_API_KEY = f.read().strip()

openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ───────────────── Logger ─────────────────
handler = RotatingFileHandler('bot.log', maxBytes=1_000_000, backupCount=5, encoding='utf-8')
logging.basicConfig(level=logging.INFO, handlers=[handler])
logging.getLogger('discord').setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# チャンネル型の許可タプル (Text / Thread / Stage)
MESSAGE_CHANNEL_TYPES: tuple[type, ...] = (
    discord.TextChannel,
    discord.Thread,
    discord.StageChannel,
    discord.VoiceChannel,
)

# ───────────────── Logger ─────────────────

# ───────────────── Discord 初期化 ─────────────────
intents = discord.Intents.default()
intents.message_content = True          # メッセージ内容を取得
intents.reactions = True 
intents.members   = True        # 追加
intents.presences = True 
intents.voice_states    = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

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


class SlashMessage:
    """Wrap discord.Interaction to mimic discord.Message."""
    def __init__(self, interaction: discord.Interaction):
        self._itx = interaction
        self.channel = interaction.channel
        self.guild = interaction.guild
        self.author = interaction.user
        self.attachments: list[discord.Attachment] = []

    async def reply(self, *args, **kwargs):
        if not self._itx.response.is_done():
            await self._itx.response.send_message(*args, **kwargs)
        else:
            await self._itx.followup.send(*args, **kwargs)

    async def add_reaction(self, emoji):
        if not self._itx.response.is_done():
            await self._itx.response.send_message(emoji)
        else:
            await self._itx.followup.send(emoji)


from yt_dlp import YoutubeDL
YTDL_OPTS = {
    "quiet": True,
    "format": "bestaudio[ext=m4a]/bestaudio/best",
    "default_search": "ytsearch",
}

@dataclass
class Track:
    title: str
    url: str
    duration: int | None = None

def yt_extract(url_or_term: str) -> list[Track]:
    """URL か検索語から Track 一覧を返す (単曲の場合は長さ1)"""
    with YoutubeDL(YTDL_OPTS) as ydl:
        info = ydl.extract_info(url_or_term, download=False)
        if "entries" in info:
            if info.get("_type") == "playlist":
                results = []
                for ent in info.get("entries", []):
                    if ent:
                        results.append(Track(ent.get("title", "?"), ent.get("url", ""), ent.get("duration")))
                return results
            info = info["entries"][0]
        return [Track(info.get("title", "?"), info.get("url", ""), info.get("duration"))]


async def attachment_to_track(att: discord.Attachment) -> Track:
    """Discord 添付ファイルを一時保存して Track に変換"""
    fd, path = tempfile.mkstemp(prefix="yone_", suffix=os.path.splitext(att.filename)[1])
    os.close(fd)
    await att.save(path)
    return Track(att.filename, path)


async def attachments_to_tracks(attachments: list[discord.Attachment]) -> list[Track]:
    """複数添付ファイルを並列で Track に変換"""
    tasks = [attachment_to_track(a) for a in attachments]
    return await asyncio.gather(*tasks)


def yt_extract_multiple(urls: list[str]) -> list[Track]:
    """複数 URL を順に yt_extract して Track をまとめて返す"""
    tracks: list[Track] = []
    for url in urls:
        try:
            tracks.extend(yt_extract(url))
        except Exception as e:
            print(f"取得失敗 ({url}): {e}")
    return tracks


def is_playlist_url(url: str) -> bool:
    """URL に playlist パラメータが含まれるか簡易判定"""
    try:
        qs = parse_qs(urlparse(url).query)
        return 'list' in qs
    except Exception:
        return False


def is_http_source(path_or_url: str) -> bool:
    """http/https から始まる URL か判定"""
    return path_or_url.startswith(("http://", "https://"))


async def add_playlist_lazy(state: "MusicState", playlist_url: str,
                            voice: discord.VoiceClient,
                            channel: discord.TextChannel):
    """プレイリストの曲を逐次取得してキューへ追加"""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None,
        lambda: YoutubeDL({**YTDL_OPTS, "extract_flat": True}).extract_info(
            playlist_url, download=False)
    )
    entries = info.get("entries", [])
    await channel.send(f"⏱️ プレイリストを読み込み中... ({len(entries)}曲)")
    for ent in entries:
        if task and task.cancelled():
            break
        if not voice.is_connected():
            break
        url = ent.get("url")
        if not url:
            continue
        try:
            tracks = await loop.run_in_executor(None, yt_extract, url)
        except Exception as e:
            print(f"取得失敗 ({url}): {e}")
            continue
        if not tracks:
            continue
        state.queue.append(tracks[0])
        await refresh_queue(state)
        if not voice.is_playing() and not state.play_next.is_set():
            client.loop.create_task(state.player_loop(voice, channel))
    await channel.send(f"✅ プレイリストの読み込みが完了しました ({len(entries)}曲)", delete_after=10)


def is_playlist_url(url: str) -> bool:
    """URL に playlist パラメータが含まれるか簡易判定"""
    try:
        qs = parse_qs(urlparse(url).query)
        return 'list' in qs
    except Exception:
        return False


def is_http_source(path_or_url: str) -> bool:
    """http/https から始まる URL か判定"""
    return path_or_url.startswith(("http://", "https://"))


async def add_playlist_lazy(state: "MusicState", playlist_url: str,
                            voice: discord.VoiceClient,
                            channel: discord.TextChannel):
    """プレイリストの曲を逐次取得してキューへ追加"""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None,
        lambda: YoutubeDL({**YTDL_OPTS, "extract_flat": True}).extract_info(
            playlist_url, download=False)
    )
    entries = info.get("entries", [])
    await channel.send(f"⏱️ プレイリストを読み込み中... ({len(entries)}曲)")
    for ent in entries:
        url = ent.get("url")
        if not url:
            continue
        try:
            tracks = await loop.run_in_executor(None, yt_extract, url)
        except Exception as e:
            print(f"取得失敗 ({url}): {e}")
            continue
        if not tracks:
            continue
        state.queue.append(tracks[0])
        await refresh_queue(state)
        if not voice.is_playing() and not state.play_next.is_set():
            client.loop.create_task(state.player_loop(voice, channel))
    await channel.send(f"✅ プレイリストの読み込みが完了しました ({len(entries)}曲)", delete_after=10)


def is_playlist_url(url: str) -> bool:
    """URL に playlist パラメータが含まれるか簡易判定"""
    try:
        qs = parse_qs(urlparse(url).query)
        return 'list' in qs
    except Exception:
        return False


def is_http_source(path_or_url: str) -> bool:
    """http/https から始まる URL か判定"""
    return path_or_url.startswith(("http://", "https://"))


async def add_playlist_lazy(state: "MusicState", playlist_url: str,
                            voice: discord.VoiceClient,
                            channel: discord.TextChannel):
    """プレイリストの曲を逐次取得してキューへ追加"""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None,
        lambda: YoutubeDL({**YTDL_OPTS, "extract_flat": True}).extract_info(
            playlist_url, download=False)
    )
    entries = info.get("entries", [])
    await channel.send(f"⏱️ プレイリストを読み込み中... ({len(entries)}曲)")
    for ent in entries:
        url = ent.get("url")
        if not url:
            continue
        try:
            tracks = await loop.run_in_executor(None, yt_extract, url)
        except Exception as e:
            print(f"取得失敗 ({url}): {e}")
            continue
        if not tracks:
            continue
        state.queue.append(tracks[0])
        await refresh_queue(state)
        if not voice.is_playing() and not state.play_next.is_set():
            client.loop.create_task(state.player_loop(voice, channel))
    await channel.send(f"✅ プレイリストの読み込みが完了しました ({len(entries)}曲)", delete_after=10)


def is_playlist_url(url: str) -> bool:
    """URL に playlist パラメータが含まれるか簡易判定"""
    try:
        qs = parse_qs(urlparse(url).query)
        return 'list' in qs
    except Exception:
        return False


def is_http_url(url: str) -> bool:
    """http/https から始まる URL か判定"""
    return url.startswith("http://") or url.startswith("https://")


async def add_playlist_lazy(state: "MusicState", playlist_url: str,
                            voice: discord.VoiceClient,
                            channel: discord.TextChannel):
    """プレイリストの曲を逐次取得してキューへ追加"""
    task = asyncio.current_task()
    qs = parse_qs(urlparse(playlist_url).query)
    list_id = qs.get("list", [None])[0]
    if list_id:
        playlist_url = f"https://www.youtube.com/playlist?list={list_id}"
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(
        None,
        lambda: YoutubeDL({**YTDL_OPTS, "extract_flat": True}).extract_info(
            playlist_url, download=False)
    )
    entries = info.get("entries", [])
    if not entries:
        await channel.send("⚠️ プレイリストに曲が見つかりませんでした。", delete_after=5)
        return
    await channel.send(f"⏱️ プレイリストを読み込み中... ({len(entries)}曲)")
    for ent in entries:
        url = ent.get("url")
        if not url:
            continue
        try:
            tracks = await loop.run_in_executor(None, yt_extract, url)
        except Exception as e:
            print(f"取得失敗 ({url}): {e}")
            continue
        if not tracks:
            continue
        state.queue.append(tracks[0])
        await refresh_queue(state)
        if not voice.is_playing() and not state.play_next.is_set():
            client.loop.create_task(state.player_loop(voice, channel))
    await channel.send(f"✅ プレイリストの読み込みが完了しました ({len(entries)}曲)", delete_after=10)


def cleanup_track(track: Track | None):
    """ローカルファイルの場合は削除"""
    if track and os.path.exists(track.url):
        try:
            os.remove(track.url)
        except Exception as e:
            print(f"cleanup failed for {track.url}: {e}")


def parse_message_link(link: str) -> tuple[int, int, int] | None:
    """Discord メッセージリンクを guild, channel, message ID に分解"""
    m = re.search(r"discord(?:app)?\.com/channels/(\d+)/(\d+)/(\d+)", link)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))
    
import asyncio, collections

def fmt_time(sec: int) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def make_bar(pos: int, total: int, width: int = 15) -> str:
    if total <= 0:
        return "".ljust(width, "─")
    index = round(pos / total * (width - 1))
    return "━" * index + "⚪" + "─" * (width - index - 1)

def num_emoji(n: int) -> str:
    emojis = ["0️⃣","1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    return emojis[n] if 0 <= n < len(emojis) else f'[{n}]'

class MusicState:
    def __init__(self):
        self.queue   = collections.deque()   # 再生待ち Track 一覧
        self.loop    = 0  # 0:OFF,1:SONG,2:QUEUE
        self.auto_leave = True             # 全員退出時に自動で切断するか
        self.current: Track | None = None
        self.play_next = asyncio.Event()
        self.queue_msg: discord.Message | None = None
        self.panel_owner: int | None = None
        self.start_time: float | None = None
        self.pause_offset: float = 0.0
        self.is_paused: bool = False
        self.playlist_task: asyncio.Task | None = None

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
                    if self.queue_msg:
                        try:
                            await self.queue_msg.delete()
                        except Exception:
                            pass
                        self.queue_msg = None
                        self.panel_owner = None
                    return

            # 再生準備
            self.current = self.queue[0]
            title, url = self.current.title, self.current.url
            self.is_paused = False
            self.pause_offset = 0

            before_opts = (
                "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
                if is_http_source(url) else ""
            )
            try:
                ffmpeg_audio = discord.FFmpegPCMAudio(
                    source=url,
                    executable="ffmpeg",
                    before_options=before_opts,
                    options='-vn -loglevel warning -af "volume=0.9"'
                )
                voice.play(ffmpeg_audio, after=lambda _: self.play_next.set())
            except FileNotFoundError:
                logger.error("ffmpeg executable not found")
                await channel.send(
                    "⚠️ **ffmpeg が見つかりません** — サーバーに ffmpeg をインストールして再試行してください。",
                    delete_after=5
                )
                cleanup_track(self.queue.popleft())
                continue
            except Exception as e:
                logger.error(f"ffmpeg 再生エラー: {e}")
                await channel.send(
                    f"⚠️ `{title}` の再生に失敗しました（{e}）",
                    delete_after=5
                )
                cleanup_track(self.queue.popleft())
                continue

            self.start_time = time.time()




            # チャット通知 & Embed 更新
            await channel.send(f"▶️ **Now playing**: {title}")
            await refresh_queue(self)

            progress_task = asyncio.create_task(progress_updater(self))

            # 次曲まで待機
            await self.play_next.wait()
            progress_task.cancel()
            self.start_time = None

            # ループOFFなら再生し終えた曲をキューから外す
            if self.loop == 0 and self.queue:
                finished = self.queue.popleft()
                cleanup_track(finished)
            elif self.loop == 2 and self.queue:
                self.queue.rotate(-1)

            await refresh_queue(self)


# クラス外でOK
async def refresh_queue(state: "MusicState"):
    """既存のキュー Embed と View を最新内容に書き換える"""
    if not state.queue_msg:
        return
    try:
        vc = state.queue_msg.guild.voice_client
        if not vc or not vc.is_connected():
            await state.queue_msg.delete()
            state.queue_msg = None
            state.panel_owner = None
            return
        owner = state.panel_owner or state.queue_msg.author.id
        view = QueueRemoveView(state, vc, owner)
        await state.queue_msg.edit(embed=make_embed(state), view=view)
    except discord.HTTPException:
        pass

async def progress_updater(state: "MusicState"):
    """再生中は1秒ごとにシークバーを更新"""
    try:
        while True:
            await asyncio.sleep(1)
            await refresh_queue(state)
    except asyncio.CancelledError:
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
        super().__init__(timeout=None)
        self.invoker = invoker    # 操作できる人
        self.payload = payload    # {user, text, color}

    # ── 作った人だけ操作可能 ──
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker.id:
            await interaction.response.send_message(
                "このボタンはコマンドを実行した人だけ使えます！",
                ephemeral=True,
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
        try:
            self.payload["color"] = True
            await self._regen(inter)
        except Exception:
            await inter.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )

    @discord.ui.button(label="⚫ モノクロ", style=discord.ButtonStyle.secondary)
    async def btn_mono(self, inter: discord.Interaction, _):
        try:
            self.payload["color"] = False
            await self._regen(inter)
        except Exception:
            await inter.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )


# ──────────── 🎵  VCユーティリティ ────────────
guild_states: dict[int, "MusicState"] = {}
voice_lock = asyncio.Lock()
last_4022: dict[int, float] = {}

class YoneVoiceClient(discord.VoiceClient):
    async def poll_voice_ws(self, reconnect: bool) -> None:
        backoff = discord.utils.ExponentialBackoff()
        while True:
            try:
                await self.ws.poll_event()
            except (discord.errors.ConnectionClosed, asyncio.TimeoutError) as exc:
                if isinstance(exc, discord.errors.ConnectionClosed):
                    if exc.code in (1000, 4015):
                        logger.info('Disconnecting from voice normally, close code %d.', exc.code)
                        await self.disconnect()
                        break
                    if exc.code == 4014:
                        logger.info('Disconnected from voice by force... potentially reconnecting.')
                        successful = await self.potential_reconnect()
                        if not successful:
                            logger.info('Reconnect was unsuccessful, disconnecting from voice normally...')
                            await self.disconnect()
                            break
                        else:
                            continue
                    if exc.code == 4022:
                        last_4022[self.guild.id] = time.time()
                        logger.warning('Received 4022, suppressing reconnect for 60s')
                        await self.disconnect()
                        break
                if not reconnect:
                    await self.disconnect()
                    raise

                retry = backoff.delay()
                logger.exception('Disconnected from voice... Reconnecting in %.2fs.', retry)
                self._connected.clear()
                await asyncio.sleep(retry)
                await self.voice_disconnect()
                try:
                    await self.connect(reconnect=True, timeout=self.timeout)
                except asyncio.TimeoutError:
                    logger.warning('Could not connect to voice... Retrying...')
                    continue

async def ensure_voice(msg: discord.Message) -> discord.VoiceClient | None:
    """発話者が入っている VC へ Bot を接続（既に接続済みならそれを返す）"""
    if msg.author.voice is None or msg.author.voice.channel is None:
        await msg.reply("🎤 まず VC に入室してからコマンドを実行してね！")
        return None

    if time.time() - last_4022.get(msg.guild.id, 0) < 60:
        return None

    voice = msg.guild.voice_client
    if voice and voice.is_connected():                 # すでに接続済み
        if voice.channel != msg.author.voice.channel:  # 別チャンネルなら移動
            await voice.move_to(msg.author.voice.channel)
        return voice

    # 未接続 → 接続を試みる（10 秒タイムアウト）
    try:
        async with voice_lock:
            if msg.guild.voice_client and msg.guild.voice_client.is_connected():
                return msg.guild.voice_client
            return await asyncio.wait_for(
                msg.author.voice.channel.connect(self_deaf=True, cls=YoneVoiceClient),
                timeout=10
            )
    except discord.errors.ConnectionClosed as e:
        if e.code == 4022:
            last_4022[msg.guild.id] = time.time()
        await msg.reply("⚠️ VC への接続に失敗しました。", delete_after=5)
        return None
    except asyncio.TimeoutError:
        await msg.reply(
            "⚠️ VC への接続に失敗しました。もう一度試してね！",
            delete_after=5
        )
        return None

# ──────────── 🎵  Queue UI ここから ────────────
def make_embed(state: "MusicState") -> discord.Embed:
    emb = discord.Embed(title="🎶 Queue")

    # Now Playing
    if state.current:
        emb.add_field(name="▶️ Now Playing:", value=state.current.title, inline=False)
        if state.start_time is not None and state.current.duration:
            if state.is_paused:
                pos = int(state.pause_offset)
            else:
                pos = int(time.time() - state.start_time)
            pos = max(0, min(pos, state.current.duration))
            bar = make_bar(pos, state.current.duration)
            emb.add_field(
                name=f"[{bar}] {fmt_time(pos)} / {fmt_time(state.current.duration)}",
                value="\u200b",
                inline=False
            )
    else:
        emb.add_field(name="Now Playing", value="Nothing", inline=False)

    # Up Next
    queue_list = list(state.queue)
    if state.current in queue_list:   # どこにあっても 1 回だけ除外
        queue_list.remove(state.current)

    if queue_list:
        lines, chars = [], 0
        for i, tr in enumerate(queue_list, 1):
            line = f"{num_emoji(i)} {tr.title}"
            if chars + len(line) + 1 > 800:
                lines.append(f"…and **{len(queue_list)-i+1}** more")
                break
            lines.append(line)
            chars += len(line) + 1
        body = "\n".join(lines)
    else:
        body = "Empty"

    emb.add_field(name="Up Next", value=body, inline=False)
    loop_map = {0: "OFF", 1: "Song", 2: "Queue"}
    footer = f"Loop: {loop_map.get(state.loop, 'OFF')} | Auto Leave: {'ON' if state.auto_leave else 'OFF'}"
    emb.set_footer(text=footer)
    return emb


class ControlView(discord.ui.View):
    """再生操作やループ・自動退出の切替ボタンをまとめた View"""
    def __init__(self, state: "MusicState", vc: discord.VoiceClient, owner_id: int):
        super().__init__(timeout=None)
        self.state, self.vc, self.owner_id = state, vc, owner_id
        self._update_labels()


    def _update_labels(self):
        """各ボタンの表示を現在の状態に合わせて更新"""
        labels = {0: "OFF", 1: "Song", 2: "Queue"}
        self.loop_toggle.label = f"🔁 Loop: {labels[self.state.loop]}"
        self.leave_toggle.label = f"👋 Auto Leave: {'ON' if self.state.auto_leave else 'OFF'}"


    async def interaction_check(self, itx: discord.Interaction) -> bool:
        if itx.user.id != self.owner_id:
            await itx.response.send_message(
                "このボタンはコマンドを実行した人だけ使えます！",
                ephemeral=True,
            )
            return False
        return True

    # --- ボタン定義 ---
    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.primary)
    async def _skip(self, itx: discord.Interaction, _: discord.ui.Button):
        try:
            if self.vc.is_playing():
                self.vc.stop()
            new_view = QueueRemoveView(self.state, self.vc, self.owner_id)
            await itx.response.edit_message(embed=make_embed(self.state), view=new_view)
            self.state.queue_msg = itx.message

            self.state.panel_owner = self.owner_id
        except Exception:
            await itx.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )

    @discord.ui.button(label="🔀 Shuffle", style=discord.ButtonStyle.primary)
    async def _shuffle(self, itx: discord.Interaction, _: discord.ui.Button):
        try:
            random.shuffle(self.state.queue)
            new_view = QueueRemoveView(self.state, self.vc, self.owner_id)
            await itx.response.edit_message(embed=make_embed(self.state), view=new_view)
            self.state.queue_msg = itx.message

            self.state.panel_owner = self.owner_id

        except Exception:
            await itx.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )

    @discord.ui.button(label="⏯ Pause/Resume", style=discord.ButtonStyle.secondary)
    async def _pause_resume(self, itx: discord.Interaction, _: discord.ui.Button):
        try:
            if self.vc.is_playing():
                self.vc.pause()
                self.state.is_paused = True
                if self.state.start_time is not None:
                    self.state.pause_offset = time.time() - self.state.start_time
            elif self.vc.is_paused():
                self.vc.resume()
                self.state.is_paused = False
                if self.state.start_time is not None:
                    self.state.start_time = time.time() - self.state.pause_offset
            new_view = QueueRemoveView(self.state, self.vc, self.owner_id)
            await itx.response.edit_message(embed=make_embed(self.state), view=new_view)
            self.state.queue_msg = itx.message

            self.state.panel_owner = self.owner_id

        except Exception:
            await itx.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )

    @discord.ui.button(label="🔁 Loop: OFF", style=discord.ButtonStyle.success)
    async def loop_toggle(self, itx: discord.Interaction, btn: discord.ui.Button):
        try:

            self.state.loop = (self.state.loop + 1) % 3
            self._update_labels()
            await itx.response.edit_message(embed=make_embed(self.state), view=self)
            self.state.queue_msg = itx.message
            self.state.panel_owner = self.owner_id

        except Exception:
            await itx.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )

    @discord.ui.button(label="👋 Auto Leave: ON", style=discord.ButtonStyle.success)
    async def leave_toggle(self, itx: discord.Interaction, btn: discord.ui.Button):
        try:

            self.state.auto_leave = not self.state.auto_leave
            self._update_labels()
            await itx.response.edit_message(embed=make_embed(self.state), view=self)
            self.state.queue_msg = itx.message
            self.state.panel_owner = self.owner_id

        except Exception:
            await itx.response.send_message(
                "⚠️ この操作パネルは無効です。\n"
                "`y!queue` で新しいパネルを表示してね！",
                ephemeral=True,
            )


# ──────────── 削除ボタン付き View ──────────
class RemoveButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(label=f"🗑 {index}", style=discord.ButtonStyle.danger, row=1 + (index - 1) // 5)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        view: QueueRemoveView = self.view  # type: ignore
        if interaction.user.id != view.owner_id:
            await interaction.response.send_message(
                "このボタンはコマンドを実行した人だけ使えます！",
                ephemeral=True,
            )
            return
        base = 1 if view.state.current and view.state.current in view.state.queue else 0
        remove_index = base + self.index - 1
        if remove_index >= len(view.state.queue):
            await interaction.response.send_message(
                "⚠️ この操作パネルは無効です。\n`y!queue` で再表示してね！",
                ephemeral=True,
            )
            return
        tr = list(view.state.queue)[remove_index]
        del view.state.queue[remove_index]
        cleanup_track(tr)
        new_view = QueueRemoveView(view.state, view.vc, view.owner_id)
        await interaction.response.edit_message(embed=make_embed(view.state), view=new_view)
        view.state.queue_msg = interaction.message
        view.state.panel_owner = view.owner_id
        await refresh_queue(view.state)


class QueueRemoveView(ControlView):
    def __init__(self, state: "MusicState", vc: discord.VoiceClient, owner_id: int):
        super().__init__(state, vc, owner_id)

        qlist = list(state.queue)
        if state.current in qlist:
            qlist.remove(state.current)
        for i, _ in enumerate(qlist[:10], 1):
            self.add_item(RemoveButton(i))



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
    view = QueueRemoveView(state, vc, msg.author.id)
    if state.queue_msg:
        try:

            await state.queue_msg.delete()
        except Exception:
            pass

    state.queue_msg = await msg.channel.send(embed=make_embed(state), view=view)
    state.panel_owner = msg.author.id


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
        def __init__(self):
            super().__init__(timeout=None)

        async def interaction_check(self, itx: discord.Interaction) -> bool:
            if itx.user.id != msg.author.id:
                await itx.response.send_message(
                    "このボタンはコマンドを実行した人だけ使えます！",
                    ephemeral=True,
                )
                return False
            return True

        @discord.ui.button(label="🎲もう一回振る", style=discord.ButtonStyle.primary)
        async def reroll(self, inter: discord.Interaction, btn: discord.ui.Button):
            try:
                new = [random.randint(1, sides) for _ in range(cnt)]
                await inter.response.edit_message(
                    content=f"🎲 {nota} → {', '.join(map(str,new))} 【合計 {sum(new)}】",
                    view=self
                )
            except Exception:
                await inter.response.send_message(
                    "⚠️ この操作パネルは無効です。\n"
                    "もう一度コマンドを実行してね！",
                    ephemeral=True,
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
            lambda: openai_client.responses.create(
                model="gpt-4.1",
                tools=[
                    {"type": "web_search_preview"},
                    {"type": "code_interpreter", "container": {"type": "auto"}},
                ],
                input=prompt,
                temperature=0.7,
            )
        )
        ans = resp.output_text.strip()

        await msg.channel.send(ans[:1900] + ("…" if len(ans) > 1900 else ""))
    except Exception as e:
        await msg.channel.send(f"エラー: {e}", delete_after=5)

# ──────────── 🎵  コマンド郡 ────────────

async def cmd_play(msg: discord.Message, query: str):
    """曲をキューに追加して再生を開始"""
    args = query.split()
    attachments = msg.attachments
    if not args and not attachments:
        await msg.reply("URLまたは添付ファイルを指定してね！")
        return

    voice = await ensure_voice(msg)
    if not voice:
        return

    state = guild_states.setdefault(msg.guild.id, MusicState())

    if state.playlist_task and not state.playlist_task.done():
        state.playlist_task.cancel()
        state.playlist_task = None

    tracks: list[Track] = []

    if attachments:
        try:
            tracks += await attachments_to_tracks(attachments)
        except Exception as e:
            await msg.reply(f"添付ファイル取得エラー: {e}", delete_after=5)
            return


    playlist_handled = False
    if args:
        if len(args) == 1 and is_playlist_url(args[0]):
            state.playlist_task = client.loop.create_task(
                add_playlist_lazy(state, args[0], voice, msg.channel)
            )
            playlist_handled = True
        else:
            url_tracks = yt_extract_multiple(args)
            if not url_tracks:
                await msg.reply("URLから曲を取得できませんでした。", delete_after=5)
            tracks += url_tracks

    if not tracks and not playlist_handled:
        return

    if tracks:
        state.queue.extend(tracks)
        await refresh_queue(state)
        await msg.channel.send(f"⏱️ **{len(tracks)}曲** をキューに追加しました！")


    # 再生していなければループを起動
    if state.queue and not voice.is_playing() and not state.play_next.is_set():
        client.loop.create_task(state.player_loop(voice, msg.channel))




async def cmd_stop(msg: discord.Message, _):
    """Bot を VC から切断し、キュー初期化"""
    if vc := msg.guild.voice_client:
        await vc.disconnect()
    state = guild_states.pop(msg.guild.id, None)
    if state:
        if state.playlist_task and not state.playlist_task.done():
            state.playlist_task.cancel()
        cleanup_track(state.current)
        for tr in state.queue:
            cleanup_track(tr)
        if state.queue_msg:
            try:
                await state.queue_msg.delete()
            except Exception:
                pass
            state.queue_msg = None
            state.panel_owner = None
    await msg.add_reaction("⏹️")


async def cmd_remove(msg: discord.Message, arg: str):
    state = guild_states.get(msg.guild.id)
    if not state or not state.queue:
        await msg.reply("キューは空だよ！")
        return
    nums = [int(x) for x in arg.split() if x.isdecimal()]
    if not nums:
        await msg.reply("番号を指定してね！")
        return
    q = list(state.queue)
    removed = []
    for i in sorted(set(nums), reverse=True):
        if 1 <= i <= len(q):
            removed.append(q.pop(i-1))
    state.queue = collections.deque(q)
    for tr in removed:
        cleanup_track(tr)
    await refresh_queue(state)


async def cmd_keep(msg: discord.Message, arg: str):
    state = guild_states.get(msg.guild.id)
    if not state or not state.queue:
        await msg.reply("キューは空だよ！")
        return
    nums = {int(x) for x in arg.split() if x.isdecimal()}
    if not nums:
        await msg.reply("番号を指定してね！")
        return
    q = list(state.queue)
    kept = []
    removed = []
    for i, tr in enumerate(q, 1):
        if i in nums:
            kept.append(tr)
        else:
            removed.append(tr)
    state.queue = collections.deque(kept)
    for tr in removed:
        cleanup_track(tr)
    await refresh_queue(state)


async def cmd_purge(msg: discord.Message, arg: str):
    """指定数またはリンク以降のメッセージを一括削除"""
    if not msg.guild:
        await msg.reply("サーバー内でのみ使用できます。")
        return

    target_channel: discord.abc.GuildChannel = msg.channel
    target_message: discord.Message | None = None
    arg = arg.strip()
    if not arg:
        await msg.reply("`y!purge <数>` または `y!purge <メッセージリンク>` の形式で指定してね！")
        return

    if arg.isdigit():
        limit = min(int(arg), 1000)
    else:
        ids = parse_message_link(arg)
        if not ids:
            await msg.reply("形式が正しくないよ！")
            return
        gid, cid, mid = ids
        if gid != msg.guild.id:
            await msg.reply("このサーバーのメッセージリンクを指定してね！")
            return
        ch = msg.guild.get_channel(cid)
        if ch is None or not isinstance(ch, MESSAGE_CHANNEL_TYPES):
            await msg.reply(
                f"リンク先チャンネルが見つかりません (取得型: {type(ch).__name__ if ch else 'None'})。"
            )
            return
        target_channel = ch
        if isinstance(ch, (discord.TextChannel, discord.Thread)):
            try:
                target_message = await ch.fetch_message(mid)
            except discord.NotFound:
                await msg.reply("指定メッセージが存在しません。")
                return
        else:
            try:
                target_message = await ch.fetch_message(mid)
            except Exception:
                await msg.reply("このチャンネル型では purge が未対応です。")
                return
        limit = None

    # 権限チェック
    perms_user = target_channel.permissions_for(msg.author)
    perms_bot = target_channel.permissions_for(msg.guild.me)
    if not (perms_user.manage_messages and perms_bot.manage_messages):
        await msg.reply("管理メッセージ権限が足りません。", delete_after=5)
        return

    deleted_total = 0
    try:
        if target_message is None:
            if hasattr(target_channel, "purge"):
                deleted = await target_channel.purge(limit=limit)
                deleted_total = len(deleted)
            else:
                msgs = [m async for m in target_channel.history(limit=limit)]
                await target_channel.delete_messages(msgs)
                deleted_total = len(msgs)
        else:
            after = target_message
            while True:
                if hasattr(target_channel, "purge"):
                    batch = await target_channel.purge(after=after, limit=100)
                else:
                    batch = [m async for m in target_channel.history(after=after, limit=100)]
                    await target_channel.delete_messages(batch)
                if not batch:
                    break
                deleted_total += len(batch)
                after = batch[-1]
            try:
                await target_message.delete()
                deleted_total += 1
            except discord.HTTPException:
                pass
    except discord.Forbidden:
        await msg.reply("権限不足で削除できませんでした。", delete_after=5)
        return

    await msg.channel.send(f"🗑️ {deleted_total} 件のメッセージを削除しました。", delete_after=5)


# ──────────── 🎵  自動切断ハンドラ ────────────

@client.event
async def on_voice_state_update(member, before, after):
    """誰かが VC から抜けた時、条件に応じて Bot を切断"""
    state = guild_states.get(member.guild.id)
    if not state:
        return

    voice: discord.VoiceClient | None = member.guild.voice_client
    if not voice or not voice.is_connected():
        return

    # VC 内のヒト(≠bot) が 0 人になった & auto_leave が有効？
    if len([m for m in voice.channel.members if not m.bot]) == 0 and state.auto_leave:
        try:
            await voice.disconnect()
        finally:
            st = guild_states.pop(member.guild.id, None)
            if st:
                if st.playlist_task and not st.playlist_task.done():
                    st.playlist_task.cancel()
                cleanup_track(st.current)
                for tr in st.queue:
                    cleanup_track(tr)
                if st.queue_msg:
                    try:
                        await st.queue_msg.delete()
                    except Exception:
                        pass
                    st.queue_msg = None
                    st.panel_owner = None


async def cmd_help(msg: discord.Message):
    await msg.channel.send(
        "**🎵 音楽機能**\n"
        "`y!play` `/play` - 曲やプレイリストを追加\n"
        "`y!queue` `/queue` - キュー表示＆操作 (Skip / Shuffle / Pause / Resume / Loop / Leave)\n"
        "   ※パネルが反応しない場合はもう一度 `y!queue` を実行してね！\n"
        "\n"
        "**💬 翻訳機能**\n"
        "国旗リアクションを付けると、そのメッセージを自動翻訳\n"
        "\n"
        "**🤖 AI/ツール**\n"
        "`y? <質問>` `/gpt <質問>` - GPT-4.1 が Web検索 & Python 実行で回答\n"
        "\n"
        "**🧑 ユーザー情報**\n"
        "`y!user <id>` `/user <id>` - プロフィールを表示\n"
        "\n"
        "**🕹️ その他**\n"
        "`y!ping` `/ping` - 応答速度\n"
        "`y!say <text>` `/say` - エコー\n"
        "`y!date` `/date` - 今日の日時\n"
        "`y!XdY` `/dice` - ダイス(例: 2d6)\n"
        "`y!purge <n|link>` `/purge` - メッセージ一括削除\n"
        "`y!help` `/help` - このヘルプ\n"
        "`y!?` - 返信で使うと名言化\n"
        "\nGPT-4.1 は Web検索と Code Interpreter を利用します。"
    )



# ───────────────── イベント ─────────────────
from discord import Activity, ActivityType, Status

# 起動時に 1 回設定
@client.event
async def on_ready():
    await client.change_presence(
        status=Status.online,
        activity=Activity(type=ActivityType.playing,
                          name="y!help で使い方を見る")
    )
    try:
        await tree.sync()
    except Exception as e:
        print("Slash command sync failed:", e)
    print("LOGIN:", client.user)

# ----- Slash command wrappers -----
@tree.command(name="ping", description="Botの応答速度を表示")
async def sc_ping(itx: discord.Interaction):
    await cmd_ping(SlashMessage(itx))


@tree.command(name="say", description="Botに発言させます")
@app_commands.describe(text="送信するテキスト")
async def sc_say(itx: discord.Interaction, text: str):
    await cmd_say(SlashMessage(itx), text)


@tree.command(name="date", description="Unix 時刻をDiscord形式で表示")
@app_commands.describe(timestamp="Unixタイムスタンプ")
async def sc_date(itx: discord.Interaction, timestamp: int | None = None):
    arg = str(timestamp) if timestamp is not None else ""
    await cmd_date(SlashMessage(itx), arg)


@tree.command(name="user", description="ユーザー情報を表示")
@app_commands.describe(target="ユーザーIDまたはメンション")
async def sc_user(itx: discord.Interaction, target: str = ""):
    await cmd_user(SlashMessage(itx), target)


@tree.command(name="dice", description="ダイスを振ります")
@app_commands.describe(nota="(例: 2d6, d20)")
async def sc_dice(itx: discord.Interaction, nota: str):
    await cmd_dice(SlashMessage(itx), nota)


@tree.command(name="gpt", description="ChatGPT に質問")
@app_commands.describe(text="質問内容")
async def sc_gpt(itx: discord.Interaction, text: str):
    await cmd_gpt(SlashMessage(itx), text)


@tree.command(name="play", description="曲を再生キューに追加")
@app_commands.describe(query="URLや検索キーワード")
async def sc_play(itx: discord.Interaction, query: str):
    await cmd_play(SlashMessage(itx), query)


@tree.command(name="queue", description="再生キューを表示")
async def sc_queue(itx: discord.Interaction):
    await cmd_queue(SlashMessage(itx), "")


@tree.command(name="remove", description="キューから曲を削除")
@app_commands.describe(numbers="削除する番号 (スペース区切り)")
async def sc_remove(itx: discord.Interaction, numbers: str):
    await cmd_remove(SlashMessage(itx), numbers)


@tree.command(name="keep", description="指定番号以外を削除")
@app_commands.describe(numbers="残す番号 (スペース区切り)")
async def sc_keep(itx: discord.Interaction, numbers: str):
    await cmd_keep(SlashMessage(itx), numbers)


@tree.command(name="purge", description="メッセージを一括削除")
@app_commands.describe(arg="削除数またはメッセージリンク")
async def sc_purge(itx: discord.Interaction, arg: str):
    await cmd_purge(SlashMessage(itx), arg)


@tree.command(name="stop", description="VC から退出")
async def sc_stop(itx: discord.Interaction):
    await cmd_stop(SlashMessage(itx), "")


@tree.command(name="help", description="コマンド一覧を表示")
async def sc_help(itx: discord.Interaction):
    await cmd_help(SlashMessage(itx))

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
            resp = openai_client.responses.create(
                model="gpt-4.1",
                instructions=(
                    f"Translate the user's message into {lang}. "
                    f"The flag emoji is {emoji}. Respond only with the translated text without the emoji."
                ),
                input=original,
                temperature=0.3,
            )
            translated = resp.output_text.strip()

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
                await message.reply(f"翻訳エラー: {e}", delete_after=5)
            except:
                await channel.send(f"翻訳エラー: {e}", delete_after=5)
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
    elif cmd == "remove":await cmd_remove(msg, arg)
    elif cmd == "keep": await cmd_keep(msg, arg)
    elif cmd == "purge":await cmd_purge(msg, arg)


# ───────────────── 起動 ─────────────────
client.run(TOKEN)

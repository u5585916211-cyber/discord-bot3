import os
import json
import time
from pathlib import Path
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# BASIC CONFIG
# =========================================================
AI_LOG_CHANNEL_ID = 1489037001322790922

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
MEMORY_PATH = DATA_DIR / "memory.json"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_ACCENT = 0x5865F2
DEFAULT_TITLE = "AI Assistant V2"
DEFAULT_FOOTER = "Private AI reply with /ask"
DEFAULT_SYSTEM_PROMPT = (
    "You are a stylish Discord AI assistant. "
    "Be helpful, clear, smart, and readable. "
    "Do not be overly long unless the user asks for detail."
)

MODES = {
    "normal": "You are helpful, friendly, and clear.",
    "funny": "You are funny, playful, and entertaining without being rude.",
    "gamer": "You are energetic, casual, gamer-style, and hype.",
    "anime": "You are expressive, dramatic, and anime-inspired, but still helpful.",
    "coder": "You are a strong coding assistant. Give practical, clean answers.",
    "custom": "Use the server custom prompt as the main style.",
}

MODE_COLORS = {
    "normal": 0x5865F2,
    "funny": 0xF1C40F,
    "gamer": 0x57F287,
    "anime": 0xEB459E,
    "coder": 0x3498DB,
    "custom": 0x9B59B6,
}


# =========================================================
# JSON HELPERS
# =========================================================
def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


config = load_json(CONFIG_PATH, {})
memory_store = load_json(MEMORY_PATH, {})


# =========================================================
# DISCORD SETUP
# =========================================================
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

ask_cooldowns = defaultdict(float)


# =========================================================
# HELPERS
# =========================================================
def guild_key(guild_id: int) -> str:
    return str(guild_id)


def ensure_guild(guild_id: int):
    gid = guild_key(guild_id)

    if gid not in config:
        config[gid] = {
            "mode": "normal",
            "accent_color": DEFAULT_ACCENT,
            "title": DEFAULT_TITLE,
            "footer": DEFAULT_FOOTER,
            "custom_prompt": "You are a premium Discord AI assistant for this server.",
            "logs_enabled": True,
            "cooldown_seconds": 6,
        }
        save_json(CONFIG_PATH, config)

    if gid not in memory_store:
        memory_store[gid] = {}
        save_json(MEMORY_PATH, memory_store)


def get_conf(guild_id: int) -> dict:
    ensure_guild(guild_id)
    return config[guild_key(guild_id)]


def get_user_memory(guild_id: int, user_id: int) -> list:
    ensure_guild(guild_id)
    gid = guild_key(guild_id)
    uid = str(user_id)

    if uid not in memory_store[gid]:
        memory_store[gid][uid] = []
        save_json(MEMORY_PATH, memory_store)

    return memory_store[gid][uid]


def set_user_memory(guild_id: int, user_id: int, messages: list):
    memory_store[guild_key(guild_id)][str(user_id)] = messages
    save_json(MEMORY_PATH, memory_store)


def push_memory(guild_id: int, user_id: int, role: str, content: str, max_items: int = 10):
    mem = get_user_memory(guild_id, user_id)
    mem.append({"role": role, "content": content[:1800]})
    set_user_memory(guild_id, user_id, mem[-max_items:])


def is_admin(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator


def split_message(text: str, limit: int = 1800) -> list[str]:
    parts = []
    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        parts.append(text[:split_at])
        text = text[split_at:].lstrip()
    if text:
        parts.append(text)
    return parts


def build_ai_embed(
    guild: discord.Guild,
    user: discord.abc.User,
    answer_chunk: str,
    mode: str,
    color: int,
    title: str,
    footer: str,
    index: int = 1,
    total: int = 1,
) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=answer_chunk,
        color=color,
    )
    embed.set_author(
        name=f"Antwort für {user}",
        icon_url=user.display_avatar.url,
    )
    embed.add_field(name="Mode", value=f"`{mode}`", inline=True)
    embed.add_field(name="Server", value=guild.name, inline=True)
    embed.add_field(name="Teil", value=f"`{index}/{total}`", inline=True)

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text=footer)
    return embed


def build_error_embed(message: str) -> discord.Embed:
    return discord.Embed(
        title="AI Fehler",
        description=message,
        color=0xED4245,
    )


def build_log_embed(
    guild: discord.Guild,
    user: discord.abc.User,
    prompt: str,
    answer: str,
    mode: str,
    color: int,
    channel_name: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 AI Ask Log",
        description="Neue private AI-Anfrage verarbeitet.",
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="👤 User",
        value=f"{user.mention}\n`{user.id}`",
        inline=True,
    )
    embed.add_field(
        name="🎭 Mode",
        value=f"`{mode}`",
        inline=True,
    )
    embed.add_field(
        name="📍 Channel",
        value=channel_name,
        inline=True,
    )
    embed.add_field(
        name="💬 Prompt",
        value=prompt[:1024] if prompt else "-",
        inline=False,
    )
    embed.add_field(
        name="🤖 Antwort",
        value=answer[:1024] if answer else "-",
        inline=False,
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.set_footer(text="AI Logs • /ask")
    return embed


# =========================================================
# AI
# =========================================================
async def call_openrouter(messages: list[dict]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt in Railway Variables.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_URL", "https://railway.app"),
        "X-Title": os.getenv("APP_NAME", "Discord AI Bot V2"),
    }

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "openrouter/free"),
        "messages": messages,
        "temperature": 0.85,
    }

    timeout = aiohttp.ClientTimeout(total=90)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload) as resp:
            text = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"OpenRouter Fehler {resp.status}: {text[:300]}")

            data = json.loads(text)

    try:
        return data["choices"][0]["message"]["content"].strip()
    except Exception:
        raise RuntimeError("AI Antwort konnte nicht gelesen werden.")


async def ask_ai(guild: discord.Guild, user: discord.abc.User, prompt: str) -> str:
    conf = get_conf(guild.id)
    mode = conf.get("mode", "normal")
    custom_prompt = conf.get("custom_prompt", "")
    mode_prompt = MODES.get(mode, MODES["normal"])

    memory = get_user_memory(guild.id, user.id)

    system_messages = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "system", "content": f"Current mode: {mode}. {mode_prompt}"},
    ]

    if mode == "custom" and custom_prompt:
        system_messages.append({"role": "system", "content": custom_prompt})

    messages = system_messages + memory + [{"role": "user", "content": prompt}]

    answer = await call_openrouter(messages)

    push_memory(guild.id, user.id, "user", prompt)
    push_memory(guild.id, user.id, "assistant", answer)

    return answer


# =========================================================
# EVENTS
# =========================================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        await bot.change_presence(activity=discord.CustomActivity(name="Use /ask"))
        print(f"Bot online als {bot.user} | Slash Commands: {len(synced)}")
    except Exception as e:
        print(f"Sync error: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    await bot.process_commands(message)


# =========================================================
# COMMANDS
# =========================================================
@bot.tree.command(name="ask", description="Sprich privat mit der AI.")
@app_commands.describe(prompt="Deine Nachricht an die AI")
async def ask(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    cooldown_seconds = int(conf.get("cooldown_seconds", 6))

    key = f"{interaction.guild.id}:{interaction.user.id}"
    now = time.time()

    if now - ask_cooldowns[key] < cooldown_seconds:
        remaining = round(cooldown_seconds - (now - ask_cooldowns[key]), 1)
        await interaction.response.send_message(
            embed=build_error_embed(f"Bitte warte noch **{remaining}s** bevor du `/ask` erneut nutzt."),
            ephemeral=True,
        )
        return

    ask_cooldowns[key] = now
    await interaction.response.defer(ephemeral=True)

    mode = conf.get("mode", "normal")
    color = conf.get("accent_color", MODE_COLORS.get(mode, DEFAULT_ACCENT))
    title = conf.get("title", DEFAULT_TITLE)
    footer = conf.get("footer", DEFAULT_FOOTER)

    try:
        answer = await ask_ai(interaction.guild, interaction.user, prompt)
    except Exception as e:
        await interaction.followup.send(
            embed=build_error_embed(f"`{str(e)[:350]}`"),
            ephemeral=True,
        )
        return

    chunks = split_message(answer)
    total = len(chunks)

    for index, chunk in enumerate(chunks, start=1):
        embed = build_ai_embed(
            guild=interaction.guild,
            user=interaction.user,
            answer_chunk=chunk,
            mode=mode,
            color=color,
            title=title,
            footer=footer,
            index=index,
            total=total,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    if conf.get("logs_enabled", True):
        log_channel = interaction.guild.get_channel(AI_LOG_CHANNEL_ID)
        if log_channel:
            log_embed = build_log_embed(
                guild=interaction.guild,
                user=interaction.user,
                prompt=prompt,
                answer=answer,
                mode=mode,
                color=color,
                channel_name=interaction.channel.mention if interaction.channel else "Unknown",
            )
            await log_channel.send(embed=log_embed)


@bot.tree.command(name="mode", description="Ändert den AI Mode.")
@app_commands.describe(mode="normal, funny, gamer, anime, coder, custom")
@app_commands.choices(mode=[
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="funny", value="funny"),
    app_commands.Choice(name="gamer", value="gamer"),
    app_commands.Choice(name="anime", value="anime"),
    app_commands.Choice(name="coder", value="coder"),
    app_commands.Choice(name="custom", value="custom"),
])
async def mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["mode"] = mode.value
    conf["accent_color"] = MODE_COLORS.get(mode.value, DEFAULT_ACCENT)
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(
        title="Mode geändert",
        description=f"Neuer Mode: **{mode.value}**",
        color=conf["accent_color"],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setaiui", description="Ändert Titel, Footer und Farbe vom AI UI.")
@app_commands.describe(
    title="Neuer Titel",
    footer="Neuer Footer",
    hex_color="Hex Farbe, z. B. 5865F2",
)
async def setaiui(interaction: discord.Interaction, title: str, footer: str, hex_color: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)

    try:
        conf["accent_color"] = int(hex_color.replace("#", ""), 16)
    except ValueError:
        await interaction.response.send_message("Ungültige Hex Farbe. Beispiel: `5865F2`", ephemeral=True)
        return

    conf["title"] = title
    conf["footer"] = footer
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(
        title="AI UI gespeichert",
        description="Titel, Footer und Farbe wurden aktualisiert.",
        color=conf["accent_color"],
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setprompt", description="Setzt den Custom Prompt für den custom Mode.")
@app_commands.describe(prompt="Der neue Custom Prompt")
async def setprompt(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["custom_prompt"] = prompt[:4000]
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(
        title="Custom Prompt gespeichert",
        description="Der Custom Prompt wurde aktualisiert.",
        color=conf.get("accent_color", DEFAULT_ACCENT),
    )
    embed.add_field(name="Preview", value=prompt[:1024], inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearhistory", description="Löscht den AI Verlauf.")
@app_commands.describe(user="Optional ein bestimmter User")
async def clearhistory(interaction: discord.Interaction, user: discord.Member | None = None):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    target = user or interaction.user
    ensure_guild(interaction.guild.id)
    memory_store[guild_key(interaction.guild.id)][str(target.id)] = []
    save_json(MEMORY_PATH, memory_store)

    embed = discord.Embed(
        title="History gelöscht",
        description=f"Der Verlauf von {target.mention} wurde gelöscht.",
        color=get_conf(interaction.guild.id).get("accent_color", DEFAULT_ACCENT),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ailogs", description="Aktiviert oder deaktiviert AI Logs.")
@app_commands.describe(enabled="true oder false")
async def ailogs(interaction: discord.Interaction, enabled: bool):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["logs_enabled"] = enabled
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(
        title="AI Logs geändert",
        description=f"Logs sind jetzt **{'aktiviert' if enabled else 'deaktiviert'}**.",
        color=conf.get("accent_color", DEFAULT_ACCENT),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="setcooldown", description="Setzt den /ask Cooldown in Sekunden.")
@app_commands.describe(seconds="Cooldown in Sekunden")
async def setcooldown(interaction: discord.Interaction, seconds: app_commands.Range[int, 1, 60]):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["cooldown_seconds"] = seconds
    save_json(CONFIG_PATH, config)

    await interaction.response.send_message(
        f"`/ask` Cooldown ist jetzt **{seconds}s**.",
        ephemeral=True,
    )


@bot.tree.command(name="aihelp", description="Zeigt alle AI Commands.")
async def aihelp(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    embed = discord.Embed(
        title="AI Bot V2 Commands",
        description="Hier sind alle wichtigen Befehle vom AI Bot.",
        color=conf.get("accent_color", DEFAULT_ACCENT),
    )
    embed.add_field(name="/ask", value="Private AI Antwort nur für dich sichtbar", inline=False)
    embed.add_field(name="/mode", value="Ändert den AI Mode", inline=False)
    embed.add_field(name="/setaiui", value="Ändert Titel, Footer und Farbe", inline=False)
    embed.add_field(name="/setprompt", value="Setzt Custom Prompt für custom Mode", inline=False)
    embed.add_field(name="/clearhistory", value="Löscht AI Verlauf", inline=False)
    embed.add_field(name="/ailogs", value="Logs an oder aus", inline=False)
    embed.add_field(name="/setcooldown", value="Setzt den /ask Cooldown", inline=False)
    embed.set_footer(text=conf.get("footer", DEFAULT_FOOTER))

    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
# RUN
# =========================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt. Setze ihn in Railway Variables.")

bot.run(DISCORD_TOKEN)

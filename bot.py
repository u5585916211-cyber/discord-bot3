import os
import json
import asyncio
from pathlib import Path
from collections import defaultdict
from io import BytesIO

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# Free AI Discord Bot
# - /ask for AI chat
# - /mode to switch personalities
# - /aichannel to enable auto AI replies in a channel
# - /image to generate images (Hugging Face Inference)
# - Auto reminder message: "Use /ask to talk"
# - Clean embed UI
# - GitHub + Railway friendly
# =========================================================

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CONFIG_PATH = DATA_DIR / "ai_config.json"
MEMORY_PATH = DATA_DIR / "ai_memory.json"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
HF_IMAGE_URL = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-dev"

AI_LOG_CHANNEL_ID = 1489037001322790922
AI_CHAT_CHANNEL_ID = 1489036537764122739

DEFAULT_SYSTEM_PROMPT = (
    "You are a stylish Discord AI assistant. Be helpful, clear, and fun. "
    "Keep answers readable and not too long unless the user asks for detail."
)

MODES = {
    "normal": "You are helpful, clean, friendly, and smart.",
    "funny": "You are funny, playful, and entertaining without being rude.",
    "gamer": "You are energetic, gamer-style, casual, and hype.",
    "anime": "You are dramatic, friendly, anime-inspired, and expressive.",
    "coder": "You are a strong programming helper who explains clearly and writes usable code.",
}


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


intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

cooldowns = defaultdict(float)
reminder_cooldowns = defaultdict(float)


# =========================================================
# Helpers
# =========================================================
def guild_key(guild_id: int) -> str:
    return str(guild_id)



def ensure_guild(guild_id: int):
    gid = guild_key(guild_id)
    if gid not in config:
        config[gid] = {
            "mode": "normal",
            "ai_channels": [AI_CHAT_CHANNEL_ID],
            "accent_color": 0x5865F2,
            "title": "AI Assistant",
            "footer": "Use /ask to talk",
            "image_enabled": True,
            "show_reminder_message": True,
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



def push_memory(guild_id: int, user_id: int, role: str, content: str, max_items: int = 10):
    mem = get_user_memory(guild_id, user_id)
    mem.append({"role": role, "content": content[:2000]})
    memory_store[guild_key(guild_id)][str(user_id)] = mem[-max_items:]
    save_json(MEMORY_PATH, memory_store)



def is_admin(interaction: discord.Interaction) -> bool:
    return isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator



def split_message(text: str, limit: int = 1900):
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



def build_embed(title: str, description: str, color: int) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_author(name="AI Assistant", icon_url="https://cdn.discordapp.com/embed/avatars/0.png")
    return embed


def build_log_embed(
    guild: discord.Guild,
    user: discord.abc.User,
    prompt: str,
    answer: str,
    mode: str,
    color: int,
    source: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 AI Ask Log",
        description="Neue AI-Anfrage verarbeitet.",
        color=color,
    )
    embed.add_field(name="👤 User", value=f"{user.mention}
`{user.id}`", inline=True)
    embed.add_field(name="🎭 Mode", value=f"`{mode}`", inline=True)
    embed.add_field(name="📍 Source", value=f"`{source}`", inline=True)
    embed.add_field(name="🏠 Server", value=guild.name, inline=False)
    embed.add_field(name="💬 Prompt", value=prompt[:1024] or "-", inline=False)
    embed.add_field(name="🤖 Antwort", value=answer[:1024] or "-", inline=False)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="AI Logs • /ask")
    return embed


async def call_openrouter(messages: list[dict]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY fehlt.")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.getenv("APP_URL", "https://railway.app"),
        "X-Title": os.getenv("APP_NAME", "Discord AI Bot"),
    }

    payload = {
        "model": os.getenv("OPENROUTER_MODEL", "openrouter/free"),
        "messages": messages,
        "temperature": 0.9,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(OPENROUTER_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"OpenRouter Fehler {resp.status}: {error_text[:400]}")
            data = await resp.json()
            return data["choices"][0]["message"]["content"].strip()


async def generate_image(prompt: str) -> bytes:
    hf_token = os.getenv("HF_TOKEN")
    if not hf_token:
        raise RuntimeError("HF_TOKEN fehlt.")

    headers = {
        "Authorization": f"Bearer {hf_token}",
        "Content-Type": "application/json",
    }
    payload = {"inputs": prompt}

    async with aiohttp.ClientSession() as session:
        async with session.post(HF_IMAGE_URL, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=180)) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"HF Image Fehler {resp.status}: {error_text[:400]}")

            content_type = resp.headers.get("content-type", "")
            if not content_type.startswith("image/"):
                error_text = await resp.text()
                raise RuntimeError(f"HF Image Antwort war kein Bild: {error_text[:400]}")

            return await resp.read()


async def ask_ai(guild: discord.Guild, user: discord.abc.User, prompt: str) -> str:
    conf = get_conf(guild.id)
    mode = conf.get("mode", "normal")
    mode_prompt = MODES.get(mode, MODES["normal"])
    user_memory = get_user_memory(guild.id, user.id)

    messages = [
        {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
        {"role": "system", "content": f"Current mode: {mode}. {mode_prompt}"},
    ]
    messages.extend(user_memory)
    messages.append({"role": "user", "content": prompt})

    answer = await call_openrouter(messages)
    push_memory(guild.id, user.id, "user", prompt)
    push_memory(guild.id, user.id, "assistant", answer)
    return answer


# =========================================================
# Events
# =========================================================
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Bot online als {bot.user} | Slash Commands synced: {len(synced)}")
        await bot.change_presence(activity=discord.CustomActivity(name="Use /ask to talk"))
    except Exception as e:
        print(f"Sync error: {e}")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return

    conf = get_conf(message.guild.id)
    ai_channels = conf.get("ai_channels", [])

    if message.channel.id in ai_channels:
        now = asyncio.get_event_loop().time()
        key = f"chat:{message.guild.id}:{message.author.id}"
        if now - cooldowns[key] < 5:
            return
        cooldowns[key] = now

        async with message.channel.typing():
            try:
                answer = await ask_ai(message.guild, message.author, message.content)
            except Exception as e:
                await message.reply(f"AI Fehler: `{str(e)[:300]}`")
                return

        color = conf.get("accent_color", 0x5865F2)
        chunks = split_message(answer)
        first = True
        for chunk in chunks:
            if first:
                embed = build_embed(conf.get("title", "AI Assistant"), chunk, color)
                embed.set_footer(text=conf.get("footer", "Use /ask to talk"))
                await message.reply(embed=embed, mention_author=False)
                first = False
            else:
                await message.channel.send(chunk)

        log_channel = message.guild.get_channel(AI_LOG_CHANNEL_ID)
        if log_channel:
            log_embed = discord.Embed(title="AI Log", color=color)
            log_embed.add_field(name="User", value=f"{message.author} ({message.author.id})", inline=False)
            log_embed.add_field(name="Channel", value=message.channel.mention, inline=False)
            log_embed.add_field(name="Prompt", value=message.content[:1024], inline=False)
            log_embed.add_field(name="Mode", value=conf.get("mode", "normal"), inline=True)
            await log_channel.send(embed=log_embed)
    else:
        if (
            conf.get("show_reminder_message", True)
            and message.channel.id == AI_CHAT_CHANNEL_ID
            and not message.content.startswith("/")
        ):
            now = asyncio.get_event_loop().time()
            key = f"reminder:{message.guild.id}:{message.channel.id}"
            if now - reminder_cooldowns[key] > 120:
                reminder_cooldowns[key] = now
                reminder = discord.Embed(
                    title="💡 AI Chat Hinweis",
                    description="Nutze **`/ask`**, wenn du privat und cleaner mit der AI schreiben willst.",
                    color=conf.get("accent_color", 0x5865F2),
                )
                if message.guild.icon:
                    reminder.set_thumbnail(url=message.guild.icon.url)
                reminder.set_footer(text="Private Antwort mit /ask")
                await message.channel.send(embed=reminder)

    await bot.process_commands(message)


# =========================================================
# Slash Commands
# =========================================================
@bot.tree.command(name="ask", description="Sprich mit der AI.")
@app_commands.describe(prompt="Deine Nachricht an die AI")
async def ask(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    conf = get_conf(interaction.guild.id)

    try:
        answer = await ask_ai(interaction.guild, interaction.user, prompt)
    except Exception as e:
        await interaction.followup.send(f"AI Fehler: `{str(e)[:300]}`", ephemeral=True)
        return

    color = conf.get("accent_color", 0x5865F2)
    chunks = split_message(answer)
    first = True
    for chunk in chunks:
        if first:
            embed = build_embed(conf.get("title", "AI Assistant"), chunk, color)
            embed.set_footer(text=conf.get("footer", "Use /ask to talk"))
            if interaction.guild.icon:
                embed.set_thumbnail(url=interaction.guild.icon.url)
            await interaction.followup.send(embed=embed, ephemeral=True)
            first = False
        else:
            await interaction.followup.send(chunk, ephemeral=True)

    log_channel = interaction.guild.get_channel(AI_LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(
            embed=build_log_embed(
                interaction.guild,
                interaction.user,
                prompt,
                answer,
                conf.get("mode", "normal"),
                color,
                "slash_ask",
            )
        )


@bot.tree.command(name="image", description="Erstellt ein Bild mit AI.")
@app_commands.describe(prompt="Bildbeschreibung")
async def image(interaction: discord.Interaction, prompt: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    if not conf.get("image_enabled", True):
        await interaction.response.send_message("Image Generation ist deaktiviert.", ephemeral=True)
        return

    await interaction.response.defer()
    try:
        image_bytes = await generate_image(prompt)
    except Exception as e:
        await interaction.followup.send(f"Image Fehler: `{str(e)[:300]}`")
        return

    file = discord.File(fp=BytesIO(image_bytes), filename="ai_image.png")
    embed = discord.Embed(title="AI Image", description=f"**Prompt:** {prompt}", color=conf.get("accent_color", 0x5865F2))
    embed.set_image(url="attachment://ai_image.png")
    embed.set_footer(text=conf.get("footer", "Use /ask to talk"))
    await interaction.followup.send(embed=embed, file=file)


@bot.tree.command(name="mode", description="Wechselt den AI Modus.")
@app_commands.describe(mode="normal, funny, gamer, anime, coder")
@app_commands.choices(mode=[
    app_commands.Choice(name="normal", value="normal"),
    app_commands.Choice(name="funny", value="funny"),
    app_commands.Choice(name="gamer", value="gamer"),
    app_commands.Choice(name="anime", value="anime"),
    app_commands.Choice(name="coder", value="coder"),
])
async def mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können den Modus ändern.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["mode"] = mode.value
    save_json(CONFIG_PATH, config)

    embed = discord.Embed(title="Mode geändert", description=f"Neuer Modus: **{mode.value}**", color=conf.get("accent_color", 0x5865F2))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="aichannel", description="Aktiviert oder deaktiviert AI Auto-Chat in einem Channel.")
@app_commands.describe(channel="Der Channel", enabled="true oder false")
async def aichannel(interaction: discord.Interaction, channel: discord.TextChannel, enabled: bool):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das ändern.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    ai_channels = conf.get("ai_channels", [])

    if enabled and channel.id not in ai_channels:
        ai_channels.append(channel.id)
    elif not enabled and channel.id in ai_channels:
        ai_channels.remove(channel.id)

    conf["ai_channels"] = ai_channels
    save_json(CONFIG_PATH, config)

    state = "aktiviert" if enabled else "deaktiviert"
    await interaction.response.send_message(f"AI Auto-Chat wurde für {channel.mention} **{state}**.", ephemeral=True)


@bot.tree.command(name="setaiui", description="Ändert Titel, Farbe und Footer vom AI Bot UI.")
@app_commands.describe(title="Titel", footer="Footer", hex_color="z.B. 5865F2")
async def setaiui(interaction: discord.Interaction, title: str, footer: str, hex_color: str):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können das ändern.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    try:
        conf["accent_color"] = int(hex_color.replace("#", ""), 16)
    except ValueError:
        await interaction.response.send_message("Ungültige Farbe. Beispiel: 5865F2", ephemeral=True)
        return

    conf["title"] = title
    conf["footer"] = footer
    save_json(CONFIG_PATH, config)
    await interaction.response.send_message("AI UI wurde gespeichert.", ephemeral=True)


@bot.tree.command(name="prompthelp", description="Zeigt die AI Befehle an.")
async def prompthelp(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    embed = discord.Embed(title="AI Commands", color=conf.get("accent_color", 0x5865F2))
    embed.add_field(name="/ask", value="Sprich mit der AI", inline=False)
    embed.add_field(name="/image", value="Erstelle ein AI Bild", inline=False)
    embed.add_field(name="/mode", value="Ändere den AI Stil", inline=False)
    embed.add_field(name="/aichannel", value="Auto AI Replies für einen Channel", inline=False)
    embed.add_field(name="/setaiui", value="Passe das Bot UI an", inline=False)
    embed.set_footer(text=conf.get("footer", "Use /ask to talk"))
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clearhistory", description="Löscht den gespeicherten Verlauf eines Users.")
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
    await interaction.response.send_message(f"History von {target.mention} wurde gelöscht.", ephemeral=True)


# =========================================================
# ENV + RUN
# =========================================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt. Setze ihn in Railway.")

bot.run(DISCORD_TOKEN)

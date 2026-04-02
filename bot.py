import os
import json
import asyncio
from pathlib import Path
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# =========================
# CONFIG
# =========================
AI_LOG_CHANNEL_ID = 1489037001322790922
AI_CHAT_CHANNEL_ID = 1489036537764122739

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

CONFIG_PATH = DATA_DIR / "config.json"
MEMORY_PATH = DATA_DIR / "memory.json"

# =========================
# LOAD / SAVE
# =========================
def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

config = load_json(CONFIG_PATH, {})
memory = load_json(MEMORY_PATH, {})

# =========================
# BOT
# =========================
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

cooldowns = defaultdict(float)
reminder_cd = defaultdict(float)

# =========================
# HELPERS
# =========================
def get_conf(guild_id):
    gid = str(guild_id)
    if gid not in config:
        config[gid] = {
            "mode": "normal",
            "accent": 0x5865F2
        }
    return config[gid]

def get_memory(guild_id, user_id):
    gid = str(guild_id)
    uid = str(user_id)
    memory.setdefault(gid, {})
    memory[gid].setdefault(uid, [])
    return memory[gid][uid]

def push_memory(guild_id, user_id, role, content):
    mem = get_memory(guild_id, user_id)
    mem.append({"role": role, "content": content[:1000]})
    memory[str(guild_id)][str(user_id)] = mem[-10:]
    save_json(MEMORY_PATH, memory)

def split(text, size=1800):
    return [text[i:i+size] for i in range(0, len(text), size)]

# =========================
# AI CALL
# =========================
async def ask_ai(guild, user, prompt):
    api = os.getenv("OPENROUTER_API_KEY")

    messages = get_memory(guild.id, user.id)
    messages.append({"role": "user", "content": prompt})

    async with aiohttp.ClientSession() as s:
        async with s.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api}",
                "Content-Type": "application/json"
            },
            json={
                "model": "openrouter/free",
                "messages": messages
            }
        ) as r:
            data = await r.json()
            answer = data["choices"][0]["message"]["content"]

    push_memory(guild.id, user.id, "user", prompt)
    push_memory(guild.id, user.id, "assistant", answer)

    return answer

# =========================
# EMBEDS
# =========================
def ai_embed(guild, text, color):
    e = discord.Embed(title="🤖 AI Antwort", description=text, color=color)
    if guild.icon:
        e.set_thumbnail(url=guild.icon.url)
    e.set_footer(text="Use /ask for private chat")
    return e

def log_embed(guild, user, prompt, answer, mode):
    e = discord.Embed(title="🧠 AI LOG", color=0x5865F2)
    e.add_field(name="User", value=f"{user.mention}\n`{user.id}`", inline=True)
    e.add_field(name="Mode", value=mode, inline=True)
    e.add_field(name="Server", value=guild.name, inline=True)
    e.add_field(name="Prompt", value=prompt[:1000], inline=False)
    e.add_field(name="Antwort", value=answer[:1000], inline=False)
    return e

# =========================
# EVENTS
# =========================
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"READY: {bot.user}")

@bot.event
async def on_message(msg):
    if msg.author.bot or not msg.guild:
        return

    conf = get_conf(msg.guild.id)

    # AI CHAT CHANNEL
    if msg.channel.id == AI_CHAT_CHANNEL_ID:
        async with msg.channel.typing():
            ans = await ask_ai(msg.guild, msg.author, msg.content)

        await msg.reply(embed=ai_embed(msg.guild, ans, conf["accent"]))

        log = msg.guild.get_channel(AI_LOG_CHANNEL_ID)
        if log:
            await log.send(embed=log_embed(msg.guild, msg.author, msg.content, ans, conf["mode"]))

    else:
        # REMINDER ONLY IN AI CHANNEL
        pass

    await bot.process_commands(msg)

# =========================
# COMMANDS
# =========================
@bot.tree.command(name="ask")
@app_commands.describe(prompt="Deine Frage")
async def ask(interaction: discord.Interaction, prompt: str):
    await interaction.response.defer(ephemeral=True)

    conf = get_conf(interaction.guild.id)
    ans = await ask_ai(interaction.guild, interaction.user, prompt)

    for i, part in enumerate(split(ans)):
        if i == 0:
            await interaction.followup.send(embed=ai_embed(interaction.guild, part, conf["accent"]), ephemeral=True)
        else:
            await interaction.followup.send(part, ephemeral=True)

    log = interaction.guild.get_channel(AI_LOG_CHANNEL_ID)
    if log:
        await log.send(embed=log_embed(interaction.guild, interaction.user, prompt, ans, conf["mode"]))

# =========================
# RUN
# =========================
bot.run(os.getenv("DISCORD_TOKEN"))

import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

# =========================================================
# Discord Verify Bot
# Features:
# - Clean verify UI with persistent buttons
# - Admin slash commands
# - /setup
# - /verifypanel
# - /pollmembers
# - /pull
# - /stats
# - /restorecode
# - JSON storage (Railway + GitHub friendly)
# =========================================================

DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
CONFIG_FILE = DATA_DIR / "config.json"
VERIFIED_FILE = DATA_DIR / "verified_users.json"


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


config = load_json(CONFIG_FILE, {})
verified_users = load_json(VERIFIED_FILE, {})


# =========================
# Helpers
# =========================
def guild_key(guild_id: int) -> str:
    return str(guild_id)



def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def ensure_guild(guild_id: int):
    key = guild_key(guild_id)
    if key not in config:
        config[key] = {
            "verify_channel_id": None,
            "verify_role_id": None,
            "unverified_role_id": None,
            "log_channel_id": None,
            "panel_message_id": None,
            "panel_created_at": None,
            "accent_color": 0x57F287,
            "title": "Server Verify",
            "description": "Klicke auf **Verify**, um Zugriff auf den Server zu erhalten.",
            "footer": "Secure Verification System",
            "thumbnail_url": None,
            "image_url": None,
        }
        save_json(CONFIG_FILE, config)

    if key not in verified_users:
        verified_users[key] = []
        save_json(VERIFIED_FILE, verified_users)



def get_conf(guild_id: int) -> dict:
    ensure_guild(guild_id)
    return config[guild_key(guild_id)]



def get_verified_ids(guild_id: int) -> list[int]:
    ensure_guild(guild_id)
    return verified_users[guild_key(guild_id)]



def add_verified_user(guild_id: int, user_id: int):
    users = get_verified_ids(guild_id)
    if user_id not in users:
        users.append(user_id)
        save_json(VERIFIED_FILE, verified_users)



def build_verify_embed(guild: discord.Guild) -> discord.Embed:
    conf = get_conf(guild.id)
    color = conf.get("accent_color", 0x57F287)

    embed = discord.Embed(
        title=conf.get("title", "Server Verify"),
        description=conf.get("description", "Klicke auf Verify, um Zugriff zu bekommen."),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )

    verify_role_id = conf.get("verify_role_id")
    if verify_role_id:
        embed.add_field(
            name="Zugang",
            value=f"Nach der Verifizierung erhältst du <@&{verify_role_id}>.",
            inline=False,
        )

    embed.add_field(
        name="Hinweis",
        value="Wenn der Button nicht geht, kontaktiere das Team.",
        inline=False,
    )

    embed.set_footer(text=conf.get("footer", "Secure Verification System"))

    thumb = conf.get("thumbnail_url")
    image = conf.get("image_url")
    if thumb:
        embed.set_thumbnail(url=thumb)
    if image:
        embed.set_image(url=image)

    return embed



def build_stats_embed(guild: discord.Guild) -> discord.Embed:
    conf = get_conf(guild.id)
    verified_ids = set(get_verified_ids(guild.id))

    total_members = guild.member_count or 0
    bots = sum(1 for m in guild.members if m.bot)
    humans = total_members - bots

    verify_role = guild.get_role(conf.get("verify_role_id")) if conf.get("verify_role_id") else None
    verified_count = len([m for m in guild.members if not m.bot and m.id in verified_ids])
    unverified_count = max(humans - verified_count, 0)

    embed = discord.Embed(
        title="Member Overview",
        description="Aktuelle Server- und Verify-Statistiken",
        color=conf.get("accent_color", 0x5865F2),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Gesamtmitglieder", value=str(total_members), inline=True)
    embed.add_field(name="Menschen", value=str(humans), inline=True)
    embed.add_field(name="Bots", value=str(bots), inline=True)
    embed.add_field(name="Verifiziert", value=str(verified_count), inline=True)
    embed.add_field(name="Unverifiziert", value=str(unverified_count), inline=True)
    embed.add_field(name="Verify-Rolle", value=verify_role.mention if verify_role else "Nicht gesetzt", inline=True)
    embed.set_footer(text=f"Server: {guild.name}")
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    return embed


# =========================
# Discord bot setup
# =========================
intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# =========================
# Persistent UI Views
# =========================
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", emoji="✅", style=discord.ButtonStyle.success, custom_id="verify:confirm")
    async def verify_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
            return

        conf = get_conf(interaction.guild.id)
        verify_role_id = conf.get("verify_role_id")
        unverified_role_id = conf.get("unverified_role_id")

        if not verify_role_id:
            await interaction.response.send_message("Es ist noch keine Verify-Rolle gesetzt.", ephemeral=True)
            return

        member = interaction.user
        if not isinstance(member, discord.Member):
            await interaction.response.send_message("Mitglied konnte nicht erkannt werden.", ephemeral=True)
            return

        verify_role = interaction.guild.get_role(verify_role_id)
        if verify_role is None:
            await interaction.response.send_message("Die Verify-Rolle wurde nicht gefunden.", ephemeral=True)
            return

        if verify_role in member.roles:
            await interaction.response.send_message("Du bist bereits verifiziert.", ephemeral=True)
            return

        roles_to_add = [verify_role]
        roles_to_remove = []

        if unverified_role_id:
            unverified_role = interaction.guild.get_role(unverified_role_id)
            if unverified_role and unverified_role in member.roles:
                roles_to_remove.append(unverified_role)

        try:
            await member.add_roles(*roles_to_add, reason="User verified via button")
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason="User verified")
        except discord.Forbidden:
            await interaction.response.send_message(
                "Ich habe keine Rechte, die Rolle zu vergeben. Prüfe Rollenposition und Berechtigungen.",
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message("Die Rolle konnte gerade nicht vergeben werden.", ephemeral=True)
            return

        add_verified_user(interaction.guild.id, member.id)

        await interaction.response.send_message(
            f"✅ Du bist jetzt verifiziert und hast {verify_role.mention} erhalten.",
            ephemeral=True,
        )

        log_channel_id = conf.get("log_channel_id")
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="Verify Log",
                    description=f"{member.mention} wurde erfolgreich verifiziert.",
                    color=0x57F287,
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="User ID", value=str(member.id), inline=True)
                embed.add_field(name="Account erstellt", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
                await log_channel.send(embed=embed)

    @discord.ui.button(label="Info", emoji="ℹ️", style=discord.ButtonStyle.secondary, custom_id="verify:info")
    async def info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
            return

        conf = get_conf(interaction.guild.id)
        verify_role_id = conf.get("verify_role_id")
        verify_role_text = f"<@&{verify_role_id}>" if verify_role_id else "Nicht gesetzt"

        embed = discord.Embed(
            title="Verify Informationen",
            description="Hier findest du Infos zum Verifizierungssystem.",
            color=conf.get("accent_color", 0x5865F2),
        )
        embed.add_field(name="Verify-Rolle", value=verify_role_text, inline=False)
        embed.add_field(name="Ablauf", value="Drücke auf **Verify**, um Zugriff zu erhalten.", inline=False)
        embed.add_field(name="Probleme?", value="Melde dich beim Team, falls etwas nicht funktioniert.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Member Stats", emoji="📊", style=discord.ButtonStyle.primary, custom_id="verify:stats")
    async def stats_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
            return
        await interaction.response.send_message(embed=build_stats_embed(interaction.guild), ephemeral=True)


# =========================
# Events
# =========================
@bot.event
async def on_ready():
    bot.add_view(VerifyView())
    try:
        synced = await bot.tree.sync()
        print(f"Bot online als {bot.user} | Commands synced: {len(synced)}")
    except Exception as e:
        print(f"Command sync error: {e}")


# =========================
# Checks
# =========================
def is_admin(interaction: discord.Interaction) -> bool:
    return bool(interaction.user.guild_permissions.administrator) if isinstance(interaction.user, discord.Member) else False


# =========================
# Slash commands
# =========================
@bot.tree.command(name="setup", description="Setup für Verify-System.")
@app_commands.describe(
    verify_channel="Channel für das Verify-Panel",
    verify_role="Rolle, die nach Verify vergeben wird",
    log_channel="Optionaler Log-Channel",
    unverified_role="Optionale Rolle, die nach Verify entfernt wird",
)
async def setup(
    interaction: discord.Interaction,
    verify_channel: discord.TextChannel,
    verify_role: discord.Role,
    log_channel: discord.TextChannel | None = None,
    unverified_role: discord.Role | None = None,
):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["verify_channel_id"] = verify_channel.id
    conf["verify_role_id"] = verify_role.id
    conf["log_channel_id"] = log_channel.id if log_channel else None
    conf["unverified_role_id"] = unverified_role.id if unverified_role else None
    save_json(CONFIG_FILE, config)

    embed = discord.Embed(title="Setup gespeichert", color=0x57F287)
    embed.add_field(name="Verify-Channel", value=verify_channel.mention, inline=False)
    embed.add_field(name="Verify-Rolle", value=verify_role.mention, inline=False)
    embed.add_field(name="Log-Channel", value=log_channel.mention if log_channel else "Nicht gesetzt", inline=False)
    embed.add_field(name="Unverified-Rolle", value=unverified_role.mention if unverified_role else "Nicht gesetzt", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="verifypanel", description="Sendet das Verify-UI in den gesetzten Verify-Channel.")
async def verifypanel(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    verify_channel_id = conf.get("verify_channel_id")
    verify_role_id = conf.get("verify_role_id")

    if not verify_channel_id or not verify_role_id:
        await interaction.response.send_message("Nutze zuerst /setup.", ephemeral=True)
        return

    channel = interaction.guild.get_channel(verify_channel_id)
    if channel is None:
        await interaction.response.send_message("Verify-Channel nicht gefunden.", ephemeral=True)
        return

    embed = build_verify_embed(interaction.guild)
    message = await channel.send(embed=embed, view=VerifyView())

    conf["panel_message_id"] = message.id
    conf["panel_created_at"] = utc_now_iso()
    save_json(CONFIG_FILE, config)

    await interaction.response.send_message(f"✅ Verify-Panel wurde in {channel.mention} gesendet.", ephemeral=True)


@bot.tree.command(name="pollmembers", description="Zeigt Member- und Verify-Statistiken vom Server.")
async def pollmembers(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    await interaction.response.send_message(embed=build_stats_embed(interaction.guild), ephemeral=False)


@bot.tree.command(name="pull", description="Gibt gespeicherten verifizierten Usern ihre Rolle zurück, falls sie fehlt.")
async def pull(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    verify_role_id = conf.get("verify_role_id")
    if not verify_role_id:
        await interaction.response.send_message("Nutze zuerst /setup.", ephemeral=True)
        return

    verify_role = interaction.guild.get_role(verify_role_id)
    if verify_role is None:
        await interaction.response.send_message("Verify-Rolle nicht gefunden.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    restored = 0
    already_had = 0
    not_found = 0
    failed = 0

    for user_id in get_verified_ids(interaction.guild.id):
        member = interaction.guild.get_member(user_id)
        if member is None:
            not_found += 1
            continue
        if verify_role in member.roles:
            already_had += 1
            continue
        try:
            await member.add_roles(verify_role, reason="Restore via /pull")
            restored += 1
            await asyncio.sleep(0.35)
        except Exception:
            failed += 1

    embed = discord.Embed(
        title="Pull abgeschlossen",
        color=0x5865F2,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Wiederhergestellt", value=str(restored), inline=True)
    embed.add_field(name="Schon vorhanden", value=str(already_had), inline=True)
    embed.add_field(name="Nicht gefunden", value=str(not_found), inline=True)
    embed.add_field(name="Fehlgeschlagen", value=str(failed), inline=True)
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="Zeigt die Verify-Konfiguration und Systeminfos.")
async def stats(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)

    embed = discord.Embed(
        title="Verify System Stats",
        color=conf.get("accent_color", 0x5865F2),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(
        name="Verify-Channel",
        value=f"<#{conf['verify_channel_id']}>" if conf.get("verify_channel_id") else "Nicht gesetzt",
        inline=False,
    )
    embed.add_field(
        name="Verify-Rolle",
        value=f"<@&{conf['verify_role_id']}>" if conf.get("verify_role_id") else "Nicht gesetzt",
        inline=False,
    )
    embed.add_field(
        name="Unverified-Rolle",
        value=f"<@&{conf['unverified_role_id']}>" if conf.get("unverified_role_id") else "Nicht gesetzt",
        inline=False,
    )
    embed.add_field(
        name="Log-Channel",
        value=f"<#{conf['log_channel_id']}>" if conf.get("log_channel_id") else "Nicht gesetzt",
        inline=False,
    )
    embed.add_field(
        name="Panel Message ID",
        value=str(conf.get("panel_message_id") or "Nicht gesetzt"),
        inline=False,
    )
    embed.add_field(
        name="Gespeicherte Verifizierte",
        value=str(len(get_verified_ids(interaction.guild.id))),
        inline=False,
    )
    embed.add_field(
        name="Panel erstellt",
        value=conf.get("panel_created_at") or "Noch nicht erstellt",
        inline=False,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="restorecode", description="Erstellt einen Restore-Code für die aktuelle Verify-Konfiguration.")
async def restorecode(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    payload = {
        "guild_id": interaction.guild.id,
        "verify_channel_id": conf.get("verify_channel_id"),
        "verify_role_id": conf.get("verify_role_id"),
        "unverified_role_id": conf.get("unverified_role_id"),
        "log_channel_id": conf.get("log_channel_id"),
        "panel_message_id": conf.get("panel_message_id"),
        "verified_users": len(get_verified_ids(interaction.guild.id)),
        "generated_at": utc_now_iso(),
    }

    code = json.dumps(payload, separators=(",", ":"))
    await interaction.response.send_message(f"```json\n{code}\n```", ephemeral=True)


@bot.tree.command(name="setbranding", description="Passt Titel, Text und Design vom Verify-Panel an.")
@app_commands.describe(
    title="Titel vom Verify-Embed",
    description="Beschreibung vom Verify-Embed",
    footer="Footer Text",
    hex_color="Hex Farbe, z. B. 57F287",
    thumbnail_url="Thumbnail URL",
    image_url="Großes Bild URL",
)
async def setbranding(
    interaction: discord.Interaction,
    title: str,
    description: str,
    footer: str,
    hex_color: str | None = None,
    thumbnail_url: str | None = None,
    image_url: str | None = None,
):
    if interaction.guild is None:
        await interaction.response.send_message("Das geht nur auf einem Server.", ephemeral=True)
        return
    if not is_admin(interaction):
        await interaction.response.send_message("Nur Admins können diesen Command benutzen.", ephemeral=True)
        return

    conf = get_conf(interaction.guild.id)
    conf["title"] = title
    conf["description"] = description
    conf["footer"] = footer
    conf["thumbnail_url"] = thumbnail_url
    conf["image_url"] = image_url

    if hex_color:
        try:
            conf["accent_color"] = int(hex_color.replace("#", ""), 16)
        except ValueError:
            await interaction.response.send_message("Ungültige Hex-Farbe. Beispiel: 57F287", ephemeral=True)
            return

    save_json(CONFIG_FILE, config)

    await interaction.response.send_message("✅ Branding wurde gespeichert.", ephemeral=True)


# =========================
# Run bot
# =========================
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt. Setze ihn als Environment Variable in Railway.")

bot.run(TOKEN)

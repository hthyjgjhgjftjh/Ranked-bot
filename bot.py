import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime
import os

# --- INITIAL FALLBACK DATABASE PATH ---
db_path = '/app/data/stats.db' if os.path.exists('/app/data') else 'stats.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# --- DATABASE SCHEMA PROVISIONING ---
c.execute('CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)')
c.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value_id TEXT)')
conn.commit()

columns = [col[1] for col in c.execute("PRAGMA table_info(stats)").fetchall()]
for col_name, col_type in [("rank", "INTEGER DEFAULT 0"), ("streak", "INTEGER DEFAULT 0"),
                           ("country", "TEXT DEFAULT ''"), ("ties", "INTEGER DEFAULT 0"),
                           ("custom_name", "TEXT DEFAULT ''")]:
    if col_name not in columns:
        c.execute(f'ALTER TABLE stats ADD COLUMN {col_name} {col_type}')
conn.commit()

# --- BOT CONFIGURATION & INTENTS ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- INTERNAL PERMISSION CHECKER ---
def has_admin_access():
    async def predicate(interaction: discord.Interaction) -> bool:
        allowed_roles = [1517891459372683404, 1434191340157276351, 1523296540859301888]
        c.execute('SELECT value_id FROM config WHERE key = "access_role_id"')
        row = c.fetchone()
        if row and row[0]:
            allowed_roles.append(int(row[0]))
        user_role_ids = [role.id for role in interaction.user.roles]
        if any(r in user_role_ids for r in allowed_roles) or interaction.user.guild_permissions.administrator:
            return True
        raise app_commands.MissingAnyRole(allowed_roles)
    return app_commands.check(predicate)

# --- UTILITY HELPERS ---
def get_flag_emoji(country_input: str) -> str:
    if not country_input: return ""
    if len(country_input) > 4 or ord(country_input[0]) > 127: return f"{country_input} "
    code = country_input.strip().upper()
    if len(code) == 2 and code.isalpha():
        emoji = chr(127462 + ord(code[0]) - 65) + chr(127462 + ord(code[1]) - 65)
        return f"{emoji} "
    return ""

def get_current_timestamp() -> str:
    return f"Last Updated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')} UTC"

async def generate_leaderboard_embed(rows, guild: discord.Guild) -> discord.Embed:
    # Added extra \n for spacing between title and content
    embed = discord.Embed(title=f"🏆 **GLOBAL LEADERBOARD | 1-16** 🏆\n\n", color=discord.Color.gold())
    if not rows:
        embed.description = "The leaderboard is currently empty."
    else:
        description = ""
        for index, (uid, rank, streak, country, custom_name) in enumerate(rows):
            if index == 0: medal = "🥇 "
            elif index == 1: medal = "🥈 "
            elif index == 2: medal = "🥉 "
            else: medal = f"**{rank}:** "
            
            user = guild.get_member(uid) or await bot.fetch_user(uid)
            name_text = custom_name if custom_name and custom_name.strip() else (user.display_name if user else "Unknown")
            name_display = f"**{name_text}** (<@{uid}>)"
            flag = get_flag_emoji(country)
            streak_tag = f" | 🔥 **{streak}x**" if streak >= 2 else ""
            # Changed to single \n to reduce space between entries
            description += f"{medal}{flag}{name_display}{streak_tag}\n"
        embed.description = description.strip()

    c.execute('SELECT value_id FROM config WHERE key = "banner_url"')
    banner_row = c.fetchone()
    if banner_row and banner_row[0]: embed.set_image(url=banner_row[0])
    embed.set_footer(text=get_current_timestamp())
    return embed

async def update_live_leaderboard(guild: discord.Guild):
    c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
    rows = c.fetchall()
    embed = await generate_leaderboard_embed(rows, guild)
    c.execute('SELECT value_id FROM config WHERE key = "channel_id"')
    channel_row = c.fetchone()
    c.execute('SELECT value_id FROM config WHERE key = "message_id"')
    message_row = c.fetchone()
    if channel_row and message_row:
        channel = guild.get_channel(int(channel_row[0]))
        if channel:
            try:
                message = await channel.fetch_message(int(message_row[0]))
                await message.edit(embed=embed)
            except: pass

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user}')

# --- COMMANDS ---
@bot.tree.command(name="set_bot_access", description="Grant bot manager access to a specific role")
@app_commands.checks.has_permissions(administrator=True)
async def set_bot_access(interaction: discord.Interaction, role: discord.Role):
    c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("access_role_id", ?)', (str(role.id),))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} granted access to {role.mention}.")

class LeaderboardGroup(app_commands.Group, name="leaderboard", description="Leaderboard configurations"):
    @app_commands.command(name="setup", description="Spawn or cycle a fresh live-updating tracking leaderboard")
    @has_admin_access()
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer()
        c.execute('SELECT value_id FROM config WHERE key = "channel_id"')
        channel_row = c.fetchone()
        c.execute('SELECT value_id FROM config WHERE key = "message_id"')
        message_row = c.fetchone()
        if channel_row and message_row:
            old_channel = interaction.guild.get_channel(int(channel_row[0]))
            if old_channel:
                try:
                    old_msg = await old_channel.fetch_message(int(message_row[0]))
                    await old_msg.delete()
                except: pass
        c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
        rows = c.fetchall()
        embed = await generate_leaderboard_embed(rows, interaction.guild)
        msg = await interaction.channel.send(embed=embed)
        c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("channel_id", ?)', (str(interaction.channel_id),))
        c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("message_id", ?)', (str(msg.id),))
        conn.commit()
        await interaction.followup.send(f"✅ {interaction.user.mention} spawned a new leaderboard.")

    @app_commands.command(name="banner", description="Update the Global Leaderboard media link banner")
    @has_admin_access()
    async def banner(self, interaction: discord.Interaction, url: str):
        c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("banner_url", ?)', (url,))
        conn.commit()
        await interaction.response.send_message(f"✅ {interaction.user.mention} updated the banner.")
        await update_live_leaderboard(interaction.guild)

    @app_commands.command(name="view", description="Check current leaderboard standings")
    async def view(self, interaction: discord.Interaction):
        await interaction.response.defer()
        c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
        rows = c.fetchall()
        embed = await generate_leaderboard_embed(rows, interaction.guild)
        await interaction.followup.send(embed=embed)

bot.tree.add_command(LeaderboardGroup())

@bot.tree.command(name="set_lb_position", description="Add/Move user to a specific leaderboard position")
@has_admin_access()
async def set_lb_position(interaction: discord.Interaction, user: discord.Member, position: int, country: str = "", custom_name: str = ""):
    if not (1 <= position <= 16):
        await interaction.response.send_message("❌ Position must be 1-16.", ephemeral=True)
        return
    await interaction.response.defer()
    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    existing_row = c.fetchone()
    if existing_row and existing_row[0] > 0:
        c.execute('UPDATE stats SET rank = 0 WHERE user_id = ?', (user.id,))
        c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (existing_row[0],))
    c.execute('UPDATE stats SET rank = rank + 1 WHERE rank >= ?', (position,))
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET rank = ?, country = ?, custom_name = ? WHERE user_id = ?', (position, country, custom_name, user.id))
    c.execute('UPDATE stats SET rank = 0 WHERE rank > 16')
    conn.commit()
    await interaction.followup.send(f"✅ {interaction.user.mention} set {user.mention} to rank {position}.")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_lb_position", description="Remove a user from the leaderboard")
@has_admin_access()
async def remove_lb_position(interaction: discord.Interaction, user: discord.Member):
    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    row = c.fetchone()
    if not row or row[0] == 0:
        await interaction.response.send_message(f"❌ {user.mention} is not ranked.", ephemeral=True)
        return
    await interaction.response.defer()
    c.execute('UPDATE stats SET rank = 0 WHERE user_id = ?', (user.id,))
    c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (row[0],))
    conn.commit()
    await interaction.followup.send(f"✅ {interaction.user.mention} removed {user.mention} from the leaderboard.")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="reset_stats", description="Wipe a user's stats")
@has_admin_access()
async def reset_stats(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer()
    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    row = c.fetchone()
    if row and row[0] > 0:
        c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (row[0],))
    c.execute('UPDATE stats SET wins = 0, losses = 0, ties = 0, rank = 0, streak = 0, country = "", custom_name = "" WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.followup.send(f"🔄 {interaction.user.mention} reset stats for {user.mention}.")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_win", description="Give a user a win")
@has_admin_access()
async def add_win(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET wins = wins + 1, streak = streak + 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} added a win to {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_win", description="Remove a win")
@has_admin_access()
async def remove_win(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET wins = MAX(0, wins - 1), streak = MAX(0, streak - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} removed a win from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_loss", description="Give a user a loss")
@has_admin_access()
async def add_loss(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET losses = losses + 1, streak = 0 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} added a loss to {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_loss", description="Remove a loss")
@has_admin_access()
async def remove_loss(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET losses = MAX(0, losses - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} removed a loss from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_tie", description="Give a user a tie")
@has_admin_access()
async def add_tie(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET ties = ties + 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} added a tie to {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_tie", description="Remove a tie")
@has_admin_access()
async def remove_tie(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET ties = MAX(0, ties - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} removed a tie from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_streak", description="Set a user's win streak")
@has_admin_access()
async def set_streak(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET streak = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} set {user.mention}'s streak to {amount}x!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_wins", description="Set a user's wins")
@has_admin_access()
async def set_wins(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET wins = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} set {user.mention}'s wins to {amount}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_loss", description="Set a user's losses")
@has_admin_access()
async def set_loss(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET losses = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} set {user.mention}'s losses to {amount}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_ties", description="Set a user's ties")
@has_admin_access()
async def set_ties(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id) VALUES (?)', (user.id,))
    c.execute('UPDATE stats SET ties = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"✅ {interaction.user.mention} set {user.mention}'s ties to {amount}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="stats", description="Check stats")
async def stats(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    c.execute('SELECT wins, losses, rank, streak, country, ties, custom_name FROM stats WHERE user_id = ?', (target.id,))
    row = c.fetchone()
    if not row:
        await interaction.response.send_message("No stats found.")
        return
    wins, losses, rank, streak, country, ties, custom_name = row
    total = wins + losses + ties
    win_pct = (wins / total * 100) if total > 0 else 0
    embed = discord.Embed(title="Ranked Tracker", color=discord.Color.blue())
    display_title = f"{custom_name} {target.mention}" if custom_name else target.mention
    embed.description = f"Stats for {get_flag_emoji(country)}{display_title}"
    embed.add_field(name="Rank", value=str(rank) if rank > 0 else "Unranked", inline=True)
    embed.add_field(name="Streak", value=f"{streak}x" if streak >= 2 else str(streak), inline=True)
    embed.add_field(name="Wins", value=str(wins), inline=True)
    embed.add_field(name="Losses", value=str(losses), inline=True)
    embed.add_field(name="Ties", value=str(ties), inline=True)
    embed.add_field(name="Win Rate", value=f"{win_pct:.1f}%", inline=False)
    embed.set_footer(text=get_current_timestamp())
    await interaction.response.send_message(embed=embed)

bot.run(os.getenv('DISCORD_TOKEN'))

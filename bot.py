import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
from datetime import datetime
import os  # Added for securely loading environment variables

# --- CONFIGURATION ---
ALLOWED_ROLE_ID = 1517891459372683404
SECOND_ROLE_ID = 1434191340157276351
LEADERBOARD_BANNER_URL = "https://cdn.discordapp.com/attachments/1518166886750097552/1518245653443248188/rsw_banner.webp?ex=6a3937f3&is=6a37e673&hm=36209fea2c1640b27af2e81f94d27cb57290b574c75b7f130dadd17dc5b72dda"
# ---------------------

# Database setup - Using /app/data/ path for Railway Persistent Volumes
db_path = '/app/data/stats.db' if os.path.exists('/app/data') else 'stats.db'
conn = sqlite3.connect(db_path)
c = conn.cursor()

# Create baseline stats table
c.execute('CREATE TABLE IF NOT EXISTS stats (user_id INTEGER PRIMARY KEY, wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0)')

# Upgrade database schema tracking columns dynamically if needed
columns = [col[1] for col in c.execute("PRAGMA table_info(stats)").fetchall()]
if "rank" not in columns:
    c.execute('ALTER TABLE stats ADD COLUMN rank INTEGER DEFAULT 0')
if "streak" not in columns:
    c.execute('ALTER TABLE stats ADD COLUMN streak INTEGER DEFAULT 0')
if "country" not in columns:
    c.execute('ALTER TABLE stats ADD COLUMN country TEXT DEFAULT ""')
if "ties" not in columns:
    c.execute('ALTER TABLE stats ADD COLUMN ties INTEGER DEFAULT 0')
if "custom_name" not in columns:
    c.execute('ALTER TABLE stats ADD COLUMN custom_name TEXT DEFAULT ""')
conn.commit()

# Config table for managing live leaderboard messages
c.execute('CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value_id INTEGER)')
conn.commit()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True 
bot = commands.Bot(command_prefix="!", intents=intents)

# --- UTILITY HELPER ---
def get_flag_emoji(country_input: str) -> str:
    """Converts a 2-letter country code (e.g. 'us', 'gb') into a regional indicator emoji string."""
    if not country_input:
        return ""
    
    if len(country_input) > 4 or ord(country_input[0]) > 127:
        return f"{country_input} "
        
    code = country_input.strip().upper()
    if len(code) == 2 and code.isalpha():
        emoji = chr(127462 + ord(code[0]) - 65) + chr(127462 + ord(code[1]) - 65)
        return f"{emoji} "
    
    return ""

def get_current_timestamp() -> str:
    return f"Last Updated: {datetime.now().strftime('%Y-%m-%d %I:%M %p')} UTC"

async def generate_leaderboard_embed(rows, guild: discord.Guild) -> discord.Embed:
    """Helper to cleanly build the leaderboard embed structure."""
    max_rank = max([row[1] for row in rows]) if rows else 16
    range_end = max(1, max_rank)
    
    embed = discord.Embed(title=f"🏆 **MAIN LEADERBOARD | 1-{range_end}** 🏆", color=discord.Color.gold())
    
    if not rows:
        embed.description = "The leaderboard is currently empty."
    else:
        description = ""
        for index, (uid, rank, streak, country, custom_name) in enumerate(rows):
            if index == 0: medal = "🥇 "
            elif index == 1: medal = "🥈 "
            elif index == 2: medal = "🥉 "
            else: medal = f"**{rank}:** "
            
            # Look up the user via cache or direct API call
            user = guild.get_member(uid)
            if not user:
                try:
                    user = await bot.fetch_user(uid)
                except discord.HTTPException:
                    user = None

            # Strictly prioritize the Custom Name + @mention display
            if custom_name and str(custom_name).strip():
                name_display = f"**{str(custom_name).strip()}** (<@{uid}>)"
            elif user:
                name_display = f"**{user.display_name}** (<@{uid}>)"
            else:
                name_display = f"**Unknown** (<@{uid}>)"

            flag = get_flag_emoji(country)
            streak_tag = f" | 🔥 **{streak}x Streak**" if streak >= 2 else ""
            description += f"> {medal}{flag}{name_display}{streak_tag}\n"
            
        embed.description = description

    if LEADERBOARD_BANNER_URL:
        embed.set_image(url=LEADERBOARD_BANNER_URL)

    embed.set_footer(text=get_current_timestamp())
    return embed

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f'Logged in as {bot.user}')

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, (app_commands.MissingRole, app_commands.MissingAnyRole)):
        await interaction.response.send_message("❌ You don't have the required role to use this command.", ephemeral=True)
    else:
        raise error

# --- LIVE REFRESH HELPER ---
async def update_live_leaderboard(guild: discord.Guild):
    """Fetches data and automatically updates the active live tracking message."""
    c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
    rows = c.fetchall()
    
    embed = await generate_leaderboard_embed(rows, guild)

    c.execute('SELECT value_id FROM config WHERE key = "channel_id"')
    channel_row = c.fetchone()
    c.execute('SELECT value_id FROM config WHERE key = "message_id"')
    message_row = c.fetchone()

    if channel_row and message_row:
        channel = guild.get_channel(channel_row[0])
        if channel:
            try:
                message = await channel.fetch_message(message_row[0])
                await message.edit(embed=embed)
            except discord.NotFound:
                pass

# --- LEADERBOARD GROUP COMMANDS ---
class LeaderboardGroup(app_commands.Group, name="leaderboard", description="Leaderboard configurations"):
    
    @app_commands.command(name="setup", description="Spawn the live-updating leaderboard tracking message")
    @app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
    async def setup(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        c.execute('SELECT value_id FROM config WHERE key = "channel_id"')
        channel_row = c.fetchone()
        c.execute('SELECT value_id FROM config WHERE key = "message_id"')
        message_row = c.fetchone()

        if channel_row and message_row:
            channel = interaction.guild.get_channel(channel_row[0])
            if channel:
                try:
                    msg = await channel.fetch_message(message_row[0])
                    await interaction.followup.send(f"❌ A leaderboard is already active! You cannot have two active at the same time.\nJump to it here: {msg.jump_url}", ephemeral=True)
                    return
                except discord.NotFound:
                    pass

        c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
        rows = c.fetchall()
        
        embed = await generate_leaderboard_embed(rows, interaction.guild)
        msg = await interaction.channel.send(embed=embed)
        
        # Save this specific message to database config for live background tracking
        c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("channel_id", ?)', (interaction.channel_id,))
        c.execute('INSERT OR REPLACE INTO config (key, value_id) VALUES ("message_id", ?)', (msg.id,))
        conn.commit()
        
        await interaction.followup.send("✅ Live tracking leaderboard spawned and configured successfully!", ephemeral=True)

    @app_commands.command(name="view", description="Check the current leaderboard standings instantly")
    async def view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        
        c.execute('SELECT user_id, rank, streak, country, custom_name FROM stats WHERE rank > 0 ORDER BY rank ASC LIMIT 16')
        rows = c.fetchall()
        
        embed = await generate_leaderboard_embed(rows, interaction.guild)
        await interaction.followup.send(embed=embed)

bot.tree.add_command(LeaderboardGroup())

# --- RANK POSITION COMMANDS ---
@bot.tree.command(name="set_lb_position", description="Add/Move user to a specific leaderboard position")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
@app_commands.describe(
    country="Optional: 2-letter code (e.g. US, GB, FR) or an emoji flag",
    custom_name="Optional: The player's clean name to show beside their @mention"
)
async def set_lb_position(interaction: discord.Interaction, user: discord.Member, position: int, country: str = "", custom_name: str = ""):
    if position < 1 or position > 16:
        await interaction.response.send_message("❌ Position must be between 1 and 16.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=False)

    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    existing_row = c.fetchone()
    if existing_row and existing_row[0] > 0:
        c.execute('UPDATE stats SET rank = 0 WHERE user_id = ?', (user.id,))
        c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (existing_row[0],))

    c.execute('UPDATE stats SET rank = rank + 1 WHERE rank >= ?', (position,))
    
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    
    c.execute('UPDATE stats SET rank = ?, country = ?, custom_name = ? WHERE user_id = ?', (position, country, custom_name, user.id))
    c.execute('UPDATE stats SET rank = 0 WHERE rank > 16')
    conn.commit()  # Forces SQLite database file write immediately
    
    await interaction.followup.send(f"Moved {user.mention} to rank {position}. Grid shifted!", ephemeral=False)
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_lb_position", description="Remove a user from the leaderboard entirely")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def remove_lb_position(interaction: discord.Interaction, user: discord.Member):
    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    row = c.fetchone()
    
    if not row or row[0] == 0:
        await interaction.response.send_message(f"❌ {user.mention} is not currently on the leaderboard.", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True)
    old_rank = row[0]
    
    c.execute('UPDATE stats SET rank = 0 WHERE user_id = ?', (user.id,))
    c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (old_rank,))
    conn.commit()
    
    await interaction.followup.send(f"✅ Removed {user.mention} from the leaderboard.", ephemeral=True)
    await update_live_leaderboard(interaction.guild)

# --- STATS RESET COMMAND ---
@bot.tree.command(name="reset_stats", description="Completely wipe a user's stats and remove them from rankings")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def reset_stats(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)
    
    c.execute('SELECT rank FROM stats WHERE user_id = ?', (user.id,))
    row = c.fetchone()
    
    if row and row[0] > 0:
        old_rank = row[0]
        c.execute('UPDATE stats SET rank = rank - 1 WHERE rank > ?', (old_rank,))
    
    c.execute('UPDATE stats SET wins = 0, losses = 0, ties = 0, rank = 0, streak = 0, country = "", custom_name = "" WHERE user_id = ?', (user.id,))
    conn.commit()
    
    await interaction.followup.send(f"🔄 Stats for {user.mention} have been reset to zero and they have been unranked.", ephemeral=True)
    await update_live_leaderboard(interaction.guild)

# --- MATCH COMPONENT COMMANDS ---
@bot.tree.command(name="set_streak", description="Manually set a user's win streak")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def set_streak(interaction: discord.Interaction, user: discord.Member, amount: int):
    if amount < 0:
        await interaction.response.send_message("❌ Streak amount cannot be negative.", ephemeral=True)
        return
        
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET streak = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    
    await interaction.response.send_message(f"Set {user.mention}'s win streak to {amount}x 🔥!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_wins", description="Manually set a user's wins")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def set_wins(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET wins = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"Set {user.mention}'s wins to {amount}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="set_loss", description="Manually set a user's losses")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def set_loss(interaction: discord.Interaction, user: discord.Member, amount: int):
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET losses = ? WHERE user_id = ?', (amount, user.id))
    conn.commit()
    await interaction.response.send_message(f"Set {user.mention}'s losses to {amount}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_win", description="Give a user a win")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def add_win(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET wins = wins + 1, streak = streak + 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Added a win to {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_win", description="Remove a win")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def remove_win(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET wins = MAX(0, wins - 1), streak = MAX(0, streak - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Removed a win from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_loss", description="Give a user a loss")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def add_loss(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET losses = losses + 1, streak = 0 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Added a loss to {user.mention}. Streak broken!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_loss", description="Remove a loss")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def remove_loss(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET losses = MAX(0, losses - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Removed a loss from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="add_tie", description="Give a user a tie")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def add_tie(interaction: discord.Interaction, user: discord.Member):
    c.execute('INSERT OR IGNORE INTO stats (user_id, wins, losses, ties, rank, streak, country, custom_name) VALUES (?, 0, 0, 0, 0, 0, "", "")', (user.id,))
    c.execute('UPDATE stats SET ties = ties + 1 WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Added a tie to {user.mention}! (Streak preserved)")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="remove_tie", description="Remove a tie")
@app_commands.checks.has_any_role(ALLOWED_ROLE_ID, SECOND_ROLE_ID)
async def remove_tie(interaction: discord.Interaction, user: discord.Member):
    c.execute('UPDATE stats SET ties = MAX(0, ties - 1) WHERE user_id = ?', (user.id,))
    conn.commit()
    await interaction.response.send_message(f"Removed a tie from {user.mention}!")
    await update_live_leaderboard(interaction.guild)

@bot.tree.command(name="stats", description="Check stats")
async def stats(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    c.execute('SELECT wins, losses, rank, streak, country, ties, custom_name FROM stats WHERE user_id = ?', (target.id,))
    row = c.fetchone()
    
    if not row:
        await interaction.response.send_message("No stats found for this user.")
        return
        
    wins, losses, rank, streak, country, ties, custom_name = row
    total = wins + losses + ties
    
    win_pct = (wins / total * 100) if total > 0 else 0
    winrate_display = f"{win_pct:.1f}%"
    
    streak_display = f"{streak}x 🔥" if streak >= 2 else f"{streak} game" if streak == 1 else f"{streak} games"
    flag = get_flag_emoji(country)

    embed = discord.Embed(title="Ranked Tracker", color=discord.Color.blue())
    display_title = f"{custom_name} {target.mention}" if custom_name else target.mention
    embed.description = f"Stats for {flag}{display_title}"
    
    embed.add_field(name="Rank", value=str(rank) if rank > 0 else "Unranked", inline=True)
    embed.add_field(name="Current Streak", value=streak_display, inline=True)
    embed.add_field(name="Wins", value=str(wins), inline=True)
    embed.add_field(name="Losses", value=str(losses), inline=True)
    embed.add_field(name="Ties", value=str(ties), inline=True)
    embed.add_field(name="Win Rate", value=winrate_display, inline=False)
    
    embed.set_footer(text=get_current_timestamp())
    await interaction.response.send_message(embed=embed)

bot.run(os.getenv('DISCORD_TOKEN'))

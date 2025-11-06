import discord
from discord import app_commands
import os
from dotenv import load_dotenv
import logging
from flask import Flask, jsonify
import threading
import time
import requests

# Load environment variables from .env file
load_dotenv()

# Configure basic logging
logging.basicConfig(level=logging.INFO)

# Get token from environment variable
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN:
    raise ValueError("Discord token not found in environment variables!")

# === You can list admin user IDs here ===
# Only these people will be allowed to use /set-news
ADMIN_IDS = [1313333441525448704]  # Replace with your actual Discord user ID(s)


# Function to read latest update text
def load_news():
    try:
        with open("update.txt", "r", encoding="utf-8") as f:
            text = f.read().strip() or "No updates yet."
            # Format the text with Discord markdown
            formatted_text = (
                text.replace("[b]", "**").replace("[/b]", "**")
                    .replace("[i]", "*").replace("[/i]", "*")
                    .replace("[br]", "\n")
                    .replace("[li]", "• ")
            )
            return formatted_text
    except FileNotFoundError:
        return "No updates yet."


# Function to write new update text
def save_news(text):
    with open("update.txt", "w", encoding="utf-8") as f:
        f.write(text)


# --- Small webserver so hosts like Render detect an open port ---
# This provides a health endpoint and binds to the PORT env var.
app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200


@app.route("/healthz")
def healthz():
    return jsonify(status="ok"), 200


def run_webserver():
    port = int(os.environ.get("PORT", 8080))
    # use_reloader=False so render / prod doesn't spawn duplicate processes
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def run_heartbeat(interval_seconds: int = 300):
    """Periodically ping the local health endpoint to keep the process active and
    provide a lightweight workload every `interval_seconds` (default 300s = 5min).
    """
    port = int(os.environ.get("PORT", 8080))
    url = f"http://127.0.0.1:{port}/healthz"
    while True:
        try:
            logging.info("Heartbeat: pinging %s", url)
            # Best-effort GET; timeout quickly if something is wrong
            requests.get(url, timeout=5)
        except Exception:
            logging.exception("Heartbeat failed to reach local health endpoint")
        time.sleep(interval_seconds)


# Create the bot
class MinimalTennisBot(discord.Client):
    def __init__(self):
        # Enable message content and server members intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ Logged in as {self.user} (ID: {self.user.id})")


bot = MinimalTennisBot()


# --- Command 1: /minimal-tennis-news ---
@bot.tree.command(
    name="minimal-tennis-news",
    description="Shows the latest development progress for Minimal Tennis 🎾"
)
async def minimal_tennis_news(interaction: discord.Interaction):
    """Send the current news. Use defer+followup to avoid Unknown interaction errors
    when the response might take longer than 3 seconds or the interaction token
    has a narrow window.
    """
    try:
        # Acknowledge quickly so Discord doesn't expire the interaction
        await interaction.response.defer(thinking=False)
        news = load_news()
        await interaction.followup.send(news)
    except discord.NotFound:
        # Interaction was not found / already expired
        logging.exception("Interaction not found when responding to /minimal-tennis-news")
    except Exception:
        # Log unexpected errors and try to notify the user (best-effort)
        logging.exception("Unhandled error while handling /minimal-tennis-news")
        try:
            # If we already deferred the interaction we must use followup.send,
            # otherwise use response.send_message.
            if interaction.response.is_done():
                await interaction.followup.send("❌ Error fetching news. Try again later.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Error fetching news. Try again later.", ephemeral=True)
        except Exception:
            # If that also fails, there's nothing more we can do here
            logging.exception("Also failed to send fallback error message for /minimal-tennis-news")


# --- Command 2: /set-news (Admin only) ---
@bot.tree.command(name="set-news", description="Update the current game progress message (admin only).")
@app_commands.describe(text="The new progress text. You can use [b], [i], [br], [ul], [li] for formatting.")
async def set_news(interaction: discord.Interaction, text: str):
    # Check admin permission
    # Allow either a server administrator or any user explicitly listed in ADMIN_IDS
    if not (interaction.user.guild_permissions.administrator or interaction.user.id in ADMIN_IDS):
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
        return

    # Save the original text (with tags)
    save_news(text)
    await interaction.response.send_message("✅ News updated successfully!", ephemeral=True)


# Run the bot
# Start the webserver in a background thread so Render (or similar) detects an open port
web_thread = threading.Thread(target=run_webserver, daemon=True)
web_thread.start()
logging.info("Started Flask webserver on port %s", os.environ.get("PORT", "8080"))

# Start heartbeat thread to run every 5 minutes (300s)
heartbeat_thread = threading.Thread(target=run_heartbeat, args=(300,), daemon=True)
heartbeat_thread.start()
logging.info("Started heartbeat thread (5 min interval)")

# Finally start the Discord bot (blocking)
bot.run(TOKEN)

#!/usr/bin/env python3
"""
Discord Money Collection Tracker Bot
- Tracks payments from 4 people (Tuesday to Saturday ONLY)
- Input format: @user 10 paid (in Discord chat)
- Automatic weekly report every Sunday
"""

import os
import json
import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
REPORT_CHANNEL_ID = os.getenv('REPORT_CHANNEL_ID')

# Configuration
DATA_FILE = "collection_data.json"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
COLLECTION_DAYS = [1, 2, 3, 4, 5]  # Tuesday=1 to Saturday=5

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)


# ============== Data Management ==============

def load_data():
    """Load existing data from JSON file."""
    if Path(DATA_FILE).exists():
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    return {"payments": [], "members": [], "report_channel": None}


def save_data(data):
    """Save data to JSON file."""
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ============== Helper Functions ==============

def is_collection_day():
    """Check if today is a collection day (Tuesday-Saturday)."""
    return datetime.now().weekday() in COLLECTION_DAYS


def is_report_day():
    """Check if today is Sunday (report day)."""
    return datetime.now().weekday() == 6


def get_week_range():
    """Get the date range for the current reporting week (Tuesday to Saturday)."""
    today = datetime.now()
    current_weekday = today.weekday()

    if current_weekday == 6:  # Sunday
        days_since_tuesday = 5
    elif current_weekday >= 1:
        days_since_tuesday = current_weekday - 1
    else:  # Monday
        days_since_tuesday = 6

    tuesday = today - timedelta(days=days_since_tuesday)
    saturday = tuesday + timedelta(days=4)

    return tuesday.date(), saturday.date()


def generate_weekly_report(data):
    """Generate the full weekly report (Tuesday to Saturday)."""
    tuesday, saturday = get_week_range()

    report = "```\n"
    report += "=" * 50 + "\n"
    report += "📊 WEEKLY MONEY COLLECTION REPORT\n"
    report += f"📆 {tuesday.strftime('%B %d')} - {saturday.strftime('%B %d, %Y')}\n"
    report += "=" * 50 + "\n\n"

    report += "📋 DAILY BREAKDOWN:\n"
    report += "-" * 40 + "\n"

    current_date = tuesday
    week_total = 0
    member_totals = {member: 0 for member in data.get("members", [])}
    member_days = {member: [] for member in data.get("members", [])}

    while current_date <= saturday:
        date_str = current_date.strftime("%Y-%m-%d")
        day_name = DAYS[current_date.weekday()]
        day_payments = [p for p in data["payments"] if p["date"] == date_str]

        report += f"\n📅 {day_name} ({date_str}):\n"

        if not day_payments:
            report += "   No payments recorded\n"
        else:
            daily_total = 0
            for p in day_payments:
                report += f"   • @{p['username']}: ${p['amount']:.2f}\n"
                daily_total += p['amount']
                week_total += p['amount']
                if p['username'] in member_totals:
                    member_totals[p['username']] += p['amount']
                    member_days[p['username']].append(day_name[:3])
            report += f"   Daily Total: ${daily_total:.2f}\n"

        current_date += timedelta(days=1)

    report += "\n" + "-" * 40 + "\n"
    report += "👥 MEMBER SUMMARY:\n"
    report += "-" * 40 + "\n"

    for member, total in member_totals.items():
        status = "✅" if total > 0 else "❌"
        days = ", ".join(member_days[member]) if member_days[member] else "None"
        report += f"{status} @{member}: ${total:.2f} (Days: {days})\n"

    report += "\n" + "=" * 50 + "\n"
    report += f"💰 WEEKLY TOTAL: ${week_total:.2f}\n"
    report += "=" * 50 + "\n"
    report += "```"

    return report


# ============== Bot Events ==============

@bot.event
async def on_ready():
    print(f"✅ {bot.user} is online!")
    print(f"📅 Today is {DAYS[datetime.now().weekday()]}")

    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")

    # Start the Sunday report task
    if not sunday_report.is_running():
        sunday_report.start()


def parse_payment_line(line, members):
    """
    Parse a single payment line like '@username 10 paid' or '@first last 10 paid'
    Returns (username, amount) or (None, None) if invalid
    """
    line = line.strip().lower()

    if not line.startswith('@'):
        return None, None

    # Remove 'paid' keyword
    line = line.replace(' paid', '').replace('paid', '')

    # Try to find amount (last number in the line)
    parts = line.split()
    if len(parts) < 2:
        return None, None

    # Find the amount (should be a number)
    amount = None
    amount_index = -1

    for i in range(len(parts) - 1, 0, -1):
        try:
            amount = float(parts[i])
            amount_index = i
            break
        except ValueError:
            continue

    if amount is None:
        return None, None

    # Username is everything between @ and the amount
    username = ' '.join(parts[0:amount_index])[1:]  # Remove @

    # Try to match with existing members (case-insensitive)
    for member in members:
        if member.lower() == username.lower():
            return member, amount

    return username, amount


@bot.event
async def on_message(message):
    """Listen for payment messages like '@user 10 paid'"""
    if message.author == bot.user:
        return

    content = message.content.strip()

    # Check if message contains @ and a number (potential payment)
    if '@' in content and any(char.isdigit() for char in content):
        # Check if it's a collection day
        if not is_collection_day():
            day_name = DAYS[datetime.now().weekday()]
            await message.channel.send(f"❌ Today is {day_name}. Collection is only **Tuesday to Saturday**!")
            await bot.process_commands(message)
            return

        data = load_data()
        members = data.get("members", [])

        # Split by lines to handle multiple payments
        lines = content.split('\n')

        recorded_payments = []
        errors = []

        for line in lines:
            line = line.strip()
            if not line.startswith('@'):
                continue

            username, amount = parse_payment_line(line, members)

            if username is None:
                continue

            # Check if member exists (case-insensitive match)
            member_match = None
            for m in members:
                if m.lower() == username.lower():
                    member_match = m
                    break

            if not member_match:
                errors.append(f"⚠️ **@{username}** not in member list")
                continue

            # Record payment
            now = datetime.now()
            payment = {
                "username": member_match,
                "amount": amount,
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "day": DAYS[now.weekday()],
                "recorded_by": str(message.author)
            }
            data["payments"].append(payment)
            recorded_payments.append(payment)

        # Save if any payments recorded
        if recorded_payments:
            save_data(data)

            # Get today's summary
            now = datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            today_payments = [p for p in data["payments"] if p["date"] == today_str]
            today_total = sum(p["amount"] for p in today_payments)

            paid_users = set(p["username"] for p in today_payments)
            pending = [m for m in members if m not in paid_users]

            # Build response
            if len(recorded_payments) == 1:
                p = recorded_payments[0]
                response = (
                    f"✅ **Payment Recorded!**\n"
                    f"👤 Member: **@{p['username']}**\n"
                    f"💵 Amount: **${p['amount']:.2f}**\n"
                    f"📅 {p['day']}, {p['date']}\n\n"
                )
            else:
                response = f"✅ **{len(recorded_payments)} Payments Recorded!**\n"
                for p in recorded_payments:
                    response += f"• @{p['username']}: ${p['amount']:.2f}\n"
                response += "\n"

            response += f"📊 **Today's Total: ${today_total:.2f}**\n"

            if pending:
                response += f"⏳ Pending: {', '.join('@' + u for u in pending)}"
            else:
                response += "🎉 All members have paid today!"

            # Add errors if any
            if errors:
                response += "\n\n" + "\n".join(errors)

            await message.channel.send(response)

        elif errors:
            await message.channel.send("\n".join(errors) + "\n\nUse `/addmember name` to add members.")

    await bot.process_commands(message)


# ============== Scheduled Tasks ==============

@tasks.loop(hours=1)
async def sunday_report():
    """Automatically post weekly report on Sunday."""
    now = datetime.now()

    # Check if it's Sunday at 9 AM
    if now.weekday() == 6 and now.hour == 9:
        data = load_data()

        # Get the report channel
        channel_id = data.get("report_channel") or REPORT_CHANNEL_ID

        if channel_id:
            channel = bot.get_channel(int(channel_id))
            if channel:
                report = generate_weekly_report(data)
                await channel.send("📢 **WEEKLY REPORT - Sunday Update**")
                await channel.send(report)
                print(f"✅ Weekly report posted to #{channel.name}")


# ============== Slash Commands ==============

@bot.tree.command(name="setup", description="Set up the bot with 4 members")
@app_commands.describe(
    member1="First member name",
    member2="Second member name",
    member3="Third member name",
    member4="Fourth member name"
)
async def setup(interaction: discord.Interaction, member1: str, member2: str, member3: str, member4: str):
    """Set up the 4 members for tracking."""
    data = load_data()
    members = [m.lower().replace("@", "") for m in [member1, member2, member3, member4]]
    data["members"] = members
    data["report_channel"] = interaction.channel_id
    save_data(data)

    await interaction.response.send_message(
        f"✅ **Bot Setup Complete!**\n\n"
        f"👥 **Members:** {', '.join('@' + m for m in members)}\n"
        f"📢 **Report Channel:** #{interaction.channel.name}\n\n"
        f"📝 **How to record payments:**\n"
        f"`@username amount paid` (e.g., `@john 50 paid`)\n\n"
        f"📅 **Schedule:**\n"
        f"• **Tue-Sat:** Collection days\n"
        f"• **Sunday 9 AM:** Automatic weekly report"
    )


@bot.tree.command(name="addmember", description="Add a new member")
@app_commands.describe(name="Member name to add")
async def addmember(interaction: discord.Interaction, name: str):
    """Add a member to the tracking list."""
    data = load_data()
    name = name.lower().replace("@", "")

    if name in data.get("members", []):
        await interaction.response.send_message(f"⚠️ **@{name}** is already in the list!")
        return

    if "members" not in data:
        data["members"] = []
    data["members"].append(name)
    save_data(data)

    await interaction.response.send_message(f"✅ Added **@{name}** to the member list!")


@bot.tree.command(name="removemember", description="Remove a member")
@app_commands.describe(name="Member name to remove")
async def removemember(interaction: discord.Interaction, name: str):
    """Remove a member from the tracking list."""
    data = load_data()
    name = name.lower().replace("@", "")

    if name not in data.get("members", []):
        await interaction.response.send_message(f"⚠️ **@{name}** is not in the list!")
        return

    data["members"].remove(name)
    save_data(data)

    await interaction.response.send_message(f"✅ Removed **@{name}** from the member list!")


@bot.tree.command(name="members", description="Show all members")
async def members(interaction: discord.Interaction):
    """Show all tracked members."""
    data = load_data()
    member_list = data.get("members", [])

    if not member_list:
        await interaction.response.send_message("❌ No members set up yet. Use `/setup` first!")
        return

    await interaction.response.send_message(
        f"👥 **Tracked Members ({len(member_list)}):**\n" +
        "\n".join(f"• @{m}" for m in member_list)
    )


@bot.tree.command(name="today", description="Show today's collection summary")
async def today(interaction: discord.Interaction):
    """Show today's payment summary."""
    data = load_data()
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    day_name = DAYS[now.weekday()]

    today_payments = [p for p in data["payments"] if p["date"] == date_str]

    response = f"📊 **Today's Summary ({day_name}, {date_str})**\n"
    response += "-" * 30 + "\n"

    if not today_payments:
        response += "No payments recorded today.\n"
    else:
        total = 0
        for p in today_payments:
            response += f"• @{p['username']}: ${p['amount']:.2f}\n"
            total += p['amount']
        response += "-" * 30 + "\n"
        response += f"**Today's Total: ${total:.2f}**\n"

    # Pending
    paid_users = set(p['username'] for p in today_payments)
    pending = [m for m in data.get("members", []) if m not in paid_users]

    if pending:
        response += f"\n⏳ **Pending:** {', '.join('@' + u for u in pending)}"

    await interaction.response.send_message(response)


@bot.tree.command(name="report", description="Generate weekly report")
async def report(interaction: discord.Interaction):
    """Generate and show the weekly report."""
    data = load_data()
    report_text = generate_weekly_report(data)

    await interaction.response.send_message(report_text)


@bot.tree.command(name="clear", description="Clear all payment data (admin only)")
async def clear(interaction: discord.Interaction):
    """Clear all payment data for new week."""
    data = load_data()
    data["payments"] = []
    save_data(data)

    await interaction.response.send_message("✅ All payment data cleared! Ready for new week.")


@bot.tree.command(name="help", description="Show bot help")
async def help_command(interaction: discord.Interaction):
    """Show help information."""
    help_text = """
**💰 Money Collection Bot - Help**

**Recording Payments (Tue-Sat only):**
Just type in chat: `@username amount paid`
Example: `@john 50 paid` or `@mary 100`

**Commands:**
• `/setup` - Set up 4 members
• `/addmember` - Add a member
• `/removemember` - Remove a member
• `/members` - Show all members
• `/today` - Today's summary
• `/report` - Weekly report
• `/clear` - Clear data for new week
• `/help` - This help message

**Schedule:**
• **Tuesday-Saturday:** Collection days
• **Sunday 9 AM:** Automatic weekly report
• **Monday:** Rest day
"""
    await interaction.response.send_message(help_text)


# ============== Run Bot ==============

if __name__ == "__main__":
    if not TOKEN:
        print("❌ Error: DISCORD_TOKEN not found in .env file!")
        print("Please create a .env file with your Discord bot token.")
        exit(1)

    bot.run(TOKEN)

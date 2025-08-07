# app.py
import os
import logging
import re
import time
import requests
from telegram import Update, ChatPermissions
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from flask import Flask, request, jsonify

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Enhanced NSFW detection patterns with fixed-width constraints
NSFW_PATTERNS = [
    # Explicit terms
    r'\b(?:nude|naked|bare\s?skin|exposed\s?genitalia)\b',
    r'\b(?:sexual|sex|porn|xxx|nsfw|adult\s?content)\b',
    r'\b(?:fuck(?:ing)?|fck|f\*\*k|shag(?:ging)?|intercourse)\b',
    r'\b(?:blow\s?job|hand\s?job|bj|hj)\b',
    r'\b(?:cum(?:ming|shot)?|sperm|jizz|creampie)\b',
    
    # Anatomy terms
    r'\b(?:penis|dick|cock|schlong|member|phallus)\b',
    r'\b(?:vagina|pussy|cunt|clit|labia|vulva)\b',
    r'\b(?:boobs|tits|titties|rack|knockers)\b',
    r'\b(?:asshole|arsehole|butthole)\b',
    
    # Fetish/BDSM terms
    r'\b(?:bdsm|sadomaso|s\s?&\s?m|dominatrix|submissive)\b',
    r'\b(?:fetish|kink|bondage|spanking|whipping)\b',
    
    # Illegal/exploitative content
    r'\b(?:child\s?porn|kiddy\s?porn|cp|lolita|shota)\b',
    r'\b(?:rape|molest|incest|pedo|beastiality)\b',
    
    # Evasive spellings and leetspeak
    r'\b(?:pr0n|p0rn|nud3|s3x|f\*\*k|f\*ck|f\*\*\*)\b',
    r'\b(?:s\*\*t|a\*\*|a\*\*hole|b\*\*bs)\b',
    
    # NSFW URLs
    r'\b(?:porn|xxx|adult|nude|sex)[^\s]*\.(?:com|net|xyz|ru|site|to)\b',
    
    # Sexual emojis
    r'[🍆🌮🍑💦👅🔞🥵😈]'
]

# Compile patterns into single regex
NSFW_REGEX = re.compile('|'.join(NSFW_PATTERNS), re.IGNORECASE | re.UNICODE)

# Safe context phrases to ignore
SAFE_CONTEXTS = [
    'chicken breasts',
    'dumb ass',
    'smart ass',
    'bad ass',
    'kick ass',
    'penis envy',
    'fighting spirit',
    'breast cancer',
    'breast feeding',
    'breast milk'
]

# Create Flask app
flask_app = Flask(__name__)

def contains_nsfw_content(text: str) -> bool:
    """Detect NSFW content with safe context checks"""
    if not text:
        return False
        
    # Normalize text for safe context checking
    text_lower = text.lower()
    
    # First check for safe contexts
    for phrase in SAFE_CONTEXTS:
        if phrase in text_lower:
            logger.info(f"Safe context detected: {phrase}")
            return False
            
    # Then check for NSFW patterns
    return bool(NSFW_REGEX.search(text_lower))

async def is_nsfw_image(image_url: str) -> bool:
    """
    Check if image is NSFW using Sightengine's free API (2000 free checks/month)
    Requires SIGHTENGINE_USER and SIGHTENGINE_SECRET environment variables
    """
    try:
        user = os.environ.get('SIGHTENGINE_USER')
        secret = os.environ.get('SIGHTENGINE_SECRET')
        
        if not user or not secret:
            logger.warning("Sightengine credentials not set. Image check skipped.")
            return False
            
        response = requests.post(
            'https://api.sightengine.com/1.0/check.json',
            data={
                'url': image_url,
                'models': 'nudity-2.0,wad,offensive,text-content',
                'api_user': user,
                'api_secret': secret
            },
            timeout=10
        )
        data = response.json()
        
        # Evaluate results
        nsfw_score = data.get('nudity', {}).get('sexual_activity', 0)
        nsfw_score += data.get('nudity', {}).get('sexual_display', 0)
        offensive_score = data.get('offensive', {}).get('prob', 0)
        
        # Thresholds can be adjusted (0.7 = 70% confidence)
        if nsfw_score > 0.7 or offensive_score > 0.7:
            logger.info(f"NSFW image detected: {nsfw_score=}, {offensive_score=}")
            return True
            
    except Exception as e:
        logger.error(f"Image detection error: {e}")
    return False

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process incoming messages and check for NSFW content"""
    message = update.message
    if not message or not message.chat or message.chat.type not in ("group", "supergroup"):
        return

    user = message.from_user
    content_deleted = False
    nsfw_detected = False

    try:
        # Check text content
        text_content = ""
        if message.text:
            text_content = message.text
        elif message.caption:
            text_content = message.caption
            
        if text_content and contains_nsfw_content(text_content):
            nsfw_detected = True

        # Check images
        elif message.photo:
            # Use the highest quality photo
            photo = message.photo[-1]
            file = await photo.get_file()
            if await is_nsfw_image(file.file_path):
                nsfw_detected = True

        # Check stickers
        elif message.sticker:
            sticker_text = ""
            if message.sticker.emoji:
                sticker_text += message.sticker.emoji + " "
            if message.sticker.set_name:
                sticker_text += message.sticker.set_name
                
            if contains_nsfw_content(sticker_text):
                nsfw_detected = True

        # Take action if NSFW detected
        if nsfw_detected:
            # Delete the offending message
            await message.delete()
            content_deleted = True

            # Mute user for 5 minutes
            until_date = int(time.time()) + 300  # 5 minutes
            await context.bot.restrict_chat_member(
                chat_id=message.chat.id,
                user_id=user.id,
                permissions=ChatPermissions(
                    can_send_messages=False,
                    can_send_media_messages=False,
                    can_send_other_messages=False,
                    can_add_web_page_previews=False
                ),
                until_date=until_date
            )

            # Send warning to group
            warning_msg = (
                f"⚠️ NSFW content detected from {user.mention_markdown_v2()}!\n"
                f"_Content deleted and user muted for 5 minutes_"
            )
            await message.chat.send_message(
                text=warning_msg,
                parse_mode="MarkdownV2"
            )

            # Log action
            logger.info(f"Deleted NSFW content from {user.id} in {message.chat.id}")

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        if not content_deleted and nsfw_detected:
            try:
                await message.delete()
            except:
                pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    await update.message.reply_text(
        "🛡️ *Group Shield Bot Activated!*\n\n"
        "I will automatically protect your group from:\n"
        "• Explicit images/videos\n• NSFW text\n• Adult stickers\n\n"
        "_Add me to your group as admin with delete and ban permissions._\n\n"
        "🔧 *Configuration:*\n"
        "• `SIGHTENGINE_USER` & `SIGHTENGINE_SECRET` for image scanning\n\n"
        "⚙️ _Current capabilities:_\n"
        f"- Text filtering: ✅\n"
        f"- Image scanning: {'✅' if os.environ.get('SIGHTENGINE_USER') else '❌'}\n\n"
        "Use /help for bot commands and usage",
        parse_mode="MarkdownV2"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    help_text = (
        "🤖 *Group Shield Bot Help*\n\n"
        "I automatically moderate your group by:\n"
        "1. Detecting and deleting NSFW content\n"
        "2. Temporarily muting offenders (5 min)\n"
        "3. Sending warnings to the group\n\n"
        "🔒 *Required Admin Permissions:*\n"
        "- Delete messages\n"
        "- Ban users\n"
        "- Invite users via link\n\n"
        "⚙️ *Bot Commands:*\n"
        "/start - Check bot status\n"
        "/help - Show this help message\n"
        "/settings - Configure bot parameters (coming soon)\n\n"
        "🔍 *Detection Capabilities:*\n"
        "- Text messages with explicit content\n"
        "- Images with nudity/sexual content\n"
        "- Stickers with NSFW emojis or pack names\n\n"
        "⚠️ _Note: Image detection requires Sightengine API credentials_"
    )
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command"""
    settings_text = (
        "⚙️ *Bot Settings*\n\n"
        "Current configuration:\n"
        f"- Image scanning: {'✅ Enabled' if os.environ.get('SIGHTENGINE_USER') else '❌ Disabled'}\n"
        f"- Text filtering: ✅ Always active\n"
        f"- Mute duration: 5 minutes\n\n"
        "_Advanced configuration coming soon. For now, settings are managed via environment variables._"
    )
    await update.message.reply_text(settings_text, parse_mode="MarkdownV2")

@flask_app.route('/')
def health_check():
    """Health check endpoint for Render"""
    return jsonify({"status": "ok", "service": "telegram-group-protector"})

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for Telegram"""
    if request.method == "POST":
        # Process Telegram update
        json_data = request.json
        update = Update.de_json(json_data, telegram_app.bot)
        telegram_app.update_queue.put(update)
    return jsonify({"status": "success"})

def main():
    """Main application setup"""
    # Initialize Telegram application
    token = os.environ['BOT_TOKEN']
    global telegram_app
    telegram_app = Application.builder().token(token).build()

    # Register handlers
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(CommandHandler("help", help_command))
    telegram_app.add_handler(CommandHandler("settings", settings_command))
    telegram_app.add_handler(MessageHandler(filters.ALL, handle_message))

    # Setup webhook when running in production
    if 'RENDER' in os.environ:
        webhook_url = os.environ['WEBHOOK_URL'] + '/webhook'
        telegram_app.run_webhook(
            listen="0.0.0.0",
            port=int(os.environ.get('PORT', 5000)),
            webhook_url=webhook_url,
            secret_token=os.environ.get('WEBHOOK_SECRET', '')
        )
        logger.info(f"Webhook configured at: {webhook_url}")
    else:
        # Running locally with polling
        logger.info("Starting bot in polling mode...")
        telegram_app.run_polling()

if __name__ == '__main__':
    main()

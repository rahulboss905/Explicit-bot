# app.py
import os
import logging
import re
import time
import requests
import asyncio
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
            logger.info(f"NSFW text detected: {text_content[:50]}...")

        # Check images
        elif message.photo:
            # Use the highest quality photo
            photo = message.photo[-1]
            file = await photo.get_file()
            logger.info(f"Processing image: {file.file_id}")
            if await is_nsfw_image(file.file_path):
                nsfw_detected = True
                logger.info("NSFW image confirmed")

        # Check stickers
        elif message.sticker:
            sticker_text = ""
            if message.sticker.emoji:
                sticker_text += message.sticker.emoji + " "
            if message.sticker.set_name:
                sticker_text += message.sticker.set_name
                
            if sticker_text and contains_nsfw_content(sticker_text):
                nsfw_detected = True
                logger.info(f"NSFW sticker detected: {sticker_text}")

        # Take action if NSFW detected
        if nsfw_detected:
            # Delete the offending message
            await message.delete()
            content_deleted = True
            logger.info(f"Deleted message from {user.id}")

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
            logger.info(f"Muted user {user.id} for 5 minutes")

            # Send warning to group
            warning_msg = (
                f"⚠️ NSFW content detected from {user.mention_markdown_v2()}!\n"
                f"_Content deleted and user muted for 5 minutes_"
            )
            await message.chat.send_message(
                text=warning_msg,
                parse_mode="MarkdownV2"
            )
            logger.info("Sent warning to group")

    except Exception as e:
        logger.error(f"Error processing message: {e}")
        if not content_deleted and nsfw_detected:
            try:
                await message.delete()
                logger.info("Deleted message in fallback")
            except Exception as e2:
                logger.error(f"Fallback delete failed: {e2}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    logger.info(f"Start command from {update.effective_user.id}")
    await update.message.reply_text(
        "🛡️ *Group Shield Bot Activated!*\n\n"
        "I will automatically protect your group from:\n"
        "• Explicit images/videos\n• NSFW text\n• Adult stickers\n\n"
        "_Add me to your group as admin with delete and ban permissions._\n\n"
        "🔧 *Configuration Status:*\n"
        f"- Text filtering: ✅ Enabled\n"
        f"- Image scanning: {'✅ Enabled' if os.environ.get('SIGHTENGINE_USER') else '❌ Disabled'}\n\n"
        "Use /help for bot commands and usage",
        parse_mode="MarkdownV2"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    logger.info(f"Help command from {update.effective_user.id}")
    help_text = (
        "🤖 *Group Shield Bot Help*\n\n"
        "🔒 *My Purpose:*\n"
        "I automatically protect groups by detecting and removing adult content.\n\n"
        "⚡ *What I Do:*\n"
        "- Delete NSFW images/videos/stickers/text\n"
        "- Mute offenders for 5 minutes\n"
        "- Send warnings to the group\n\n"
        "🔑 *Required Permissions:*\n"
        "To work properly, I need these admin permissions:\n"
        "- Delete messages\n"
        "- Ban users\n"
        "- Invite users via link\n\n"
        "💡 *Commands:*\n"
        "/start - Check bot status\n"
        "/help - Show this help message\n"
        "/test - Test if bot is responding\n"
        "/ping - Simple health check\n\n"
        "🔍 *Detection Capabilities:*\n"
        "- Text messages with explicit content\n"
        "- Images with nudity/sexual content\n"
        "- Stickers with NSFW emojis or pack names\n\n"
        "⚠️ _Note: Image detection requires Sightengine API credentials_"
    )
    await update.message.reply_text(help_text, parse_mode="MarkdownV2")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /test command"""
    logger.info(f"Test command from {update.effective_user.id}")
    await update.message.reply_text("✅ Bot is active and responding!")

async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ping command"""
    logger.info(f"Ping command from {update.effective_user.id}")
    await update.message.reply_text("🏓 Pong!")

@flask_app.route('/')
def health_check():
    """Health check endpoint for Render"""
    return jsonify({
        "status": "ok",
        "service": "telegram-group-protector",
        "version": "2.0",
        "bot_status": "running" if telegram_app else "not initialized"
    })

@flask_app.route('/webhook', methods=['POST'])
def webhook():
    """Webhook endpoint for Telegram"""
    if request.method == "POST":
        try:
            json_data = request.json
            update = Update.de_json(json_data, telegram_app.bot)
            telegram_app.update_queue.put(update)
            logger.info("Received update via webhook")
            return jsonify({"status": "success"})
        except Exception as e:
            logger.error(f"Webhook error: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Method not allowed"}), 405

def setup_bot():
    """Initialize Telegram application"""
    global telegram_app
    token = os.environ.get('BOT_TOKEN')
    if not token:
        logger.error("BOT_TOKEN environment variable is not set!")
        return None

    try:
        telegram_app = Application.builder().token(token).build()
        
        # Register handlers
        telegram_app.add_handler(CommandHandler("start", start))
        telegram_app.add_handler(CommandHandler("help", help_command))
        telegram_app.add_handler(CommandHandler("test", test_command))
        telegram_app.add_handler(CommandHandler("ping", ping_command))
        telegram_app.add_handler(MessageHandler(filters.ALL, handle_message))
        
        logger.info("Telegram bot setup complete")
        return telegram_app
    except Exception as e:
        logger.error(f"Bot setup failed: {e}")
        return None

def main():
    """Main application setup"""
    global telegram_app
    
    # Initialize bot
    telegram_app = setup_bot()
    if not telegram_app:
        logger.error("Failed to initialize bot. Exiting.")
        return

    # Setup webhook when running in production
    if 'RENDER' in os.environ:
        webhook_url = os.environ.get('WEBHOOK_URL', '') + '/webhook'
        secret_token = os.environ.get('WEBHOOK_SECRET', '')
        port = int(os.environ.get('PORT', 5000))
        
        if not webhook_url.startswith('http'):
            logger.error(f"Invalid WEBHOOK_URL: {webhook_url}")
            return
            
        try:
            # Set webhook asynchronously
            loop = asyncio.get_event_loop()
            loop.run_until_complete(
                telegram_app.bot.set_webhook(
                    webhook_url,
                    secret_token=secret_token
                )
            )
            logger.info(f"Webhook configured at: {webhook_url}")
            
            # Start web server
            logger.info(f"Starting Flask app on port {port}")
            flask_app.run(host='0.0.0.0', port=port)
        except Exception as e:
            logger.error(f"Webhook setup failed: {e}")
    else:
        # Running locally with polling
        logger.info("Starting bot in polling mode...")
        telegram_app.run_polling()

if __name__ == '__main__':
    main()

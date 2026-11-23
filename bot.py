import os
import asyncio
import logging

from motor.motor_asyncio import AsyncIOMotorClient

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, UserIsBlocked, PeerIdInvalid

logging.basicConfig(level=logging.INFO)

# =========================
# ENV
# =========================

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGO_URI = os.environ["MONGO_URI"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

# =========================
# BOT
# =========================

app = Client(
    "broadcast_v2",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# =========================
# DATABASE
# =========================

mongo = AsyncIOMotorClient(MONGO_URI)

db = mongo["broadcast_v2"]

users = db["users"]
chats = db["chats"]

# Temporary broadcast storage
pending_broadcast = {}

# =========================
# ADMIN CHECK
# =========================

def is_admin(user_id):
    return user_id == ADMIN_ID


# =========================
# START
# =========================

@app.on_message(filters.private & ~filters.bot)
async def private_messages(client, message):

    if message.from_user:

        await users.update_one(
            {"_id": message.from_user.id},
            {
                "$set": {
                    "name": message.from_user.first_name or "",
                    "username": message.from_user.username or ""
                }
            },
            upsert=True
        )

    if message.text == "/start":

        await message.reply_text(
            "👋 **Welcome!**\n\n"
            "You are connected to the bot."
        )


# =========================
# SAVE GROUPS
# =========================

@app.on_message(filters.group & ~filters.bot)
async def group_messages(client, message):

    if not message.chat:
        return

    await chats.update_one(
        {"_id": message.chat.id},
        {
            "$set": {
                "title": message.chat.title or "",
                "type": str(message.chat.type)
            }
        },
        upsert=True
    )


# =========================
# SAVE CHANNEL POSTS
# =========================

@app.on_message(filters.channel)
async def channel_messages(client, message):

    if not message.chat:
        return

    await chats.update_one(
        {"_id": message.chat.id},
        {
            "$set": {
                "title": message.chat.title or "",
                "type": str(message.chat.type)
            }
        },
        upsert=True
    )


# =========================
# ADMIN PANEL
# =========================

@app.on_message(
    filters.private
    & filters.user(ADMIN_ID)
    & filters.command("admin")
)
async def admin_panel(client, message):

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📢 Broadcast",
                    callback_data="broadcast"
                )
            ],
            [
                InlineKeyboardButton(
                    "📊 Statistics",
                    callback_data="stats"
                )
            ]
        ]
    )

    await message.reply_text(
        "🔐 **Admin Panel**\n\n"
        "Select an option:",
        reply_markup=keyboard
    )


# =========================
# CALLBACKS
# =========================

@app.on_callback_query(filters.user(ADMIN_ID))
async def callback_handler(client, callback):

    data = callback.data

    # ---------------------
    # ADMIN
    # ---------------------

    if data == "broadcast":

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "👤 Users",
                        callback_data="target_users"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "👥 Groups",
                        callback_data="target_groups"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📢 Channels",
                        callback_data="target_channels"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🌐 All",
                        callback_data="target_all"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel",
                        callback_data="cancel"
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            "📢 **Broadcast Target**\n\n"
            "Where do you want to send the message?",
            reply_markup=keyboard
        )

        await callback.answer()

    # ---------------------
    # TARGET USERS
    # ---------------------

    elif data.startswith("target_"):

        target = data.replace("target_", "")

        pending_broadcast[ADMIN_ID] = {
            "target": target,
            "waiting": True
        }

        await callback.message.edit_text(
            f"🎯 Target: **{target.upper()}**\n\n"
            "Now send the message you want to broadcast.\n\n"
            "Supported:\n"
            "📝 Text\n"
            "🖼️ Photo\n"
            "🎬 Video\n"
            "📄 Document"
        )

        await callback.answer()

    # ---------------------
    # CONFIRM
    # ---------------------

    elif data == "confirm":

        data_store = pending_broadcast.get(ADMIN_ID)

        if not data_store:
            await callback.answer(
                "❌ Broadcast expired.",
                show_alert=True
            )
            return

        data_store["waiting"] = False

        await callback.message.edit_text(
            "🚀 **Broadcast Started...**\n\n"
            "Please wait."
        )

        await do_broadcast(
            callback.message,
            data_store
        )

        pending_broadcast.pop(ADMIN_ID, None)

        await callback.answer()

    # ---------------------
    # CANCEL
    # ---------------------

    elif data == "cancel":

        pending_broadcast.pop(ADMIN_ID, None)

        await callback.message.edit_text(
            "❌ Broadcast cancelled."
        )

        await callback.answer()

    # ---------------------
    # STATS
    # ---------------------

    elif data == "stats":

        user_count = await users.count_documents({})
        chat_count = await chats.count_documents({})

        await callback.message.edit_text(
            "📊 **Bot Statistics**\n\n"
            f"👤 Users: `{user_count}`\n"
            f"👥 Groups/Channels: `{chat_count}`"
        )

        await callback.answer()


# =========================
# RECEIVE BROADCAST MESSAGE
# =========================

@app.on_message(filters.private & filters.user(ADMIN_ID))
async def receive_broadcast_message(client, message):

    data_store = pending_broadcast.get(ADMIN_ID)

    if not data_store:
        return

    if not data_store.get("waiting"):
        return

    # Don't process commands
    if message.text and message.text.startswith("/"):
        return

    data_store["message_id"] = message.id
    data_store["chat_id"] = message.chat.id

    target = data_store["target"]

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm",
                    callback_data="confirm"
                ),
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="cancel"
                )
            ]
        ]
    )

    await message.reply_text(
        f"📋 **Broadcast Preview**\n\n"
        f"🎯 Target: `{target.upper()}`\n\n"
        "The message above will be broadcast.\n"
        "Do you want to continue?",
        reply_markup=keyboard
    )


# =========================
# BROADCAST FUNCTION
# =========================

async def do_broadcast(admin_message, data_store):

    target = data_store["target"]
    message_id = data_store["message_id"]
    source_chat = data_store["chat_id"]

    sent = 0
    failed = 0

    # ---------------------
    # USERS
    # ---------------------

    if target in ["users", "all"]:

        cursor = users.find({})

        async for user in cursor:

            chat_id = user["_id"]

            try:

                await app.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat,
                    message_id=message_id
                )

                sent += 1

                await asyncio.sleep(0.05)

            except FloodWait as e:

                await asyncio.sleep(e.value)

                try:

                    await app.copy_message(
                        chat_id=chat_id,
                        from_chat_id=source_chat,
                        message_id=message_id
                    )

                    sent += 1

                except Exception:

                    failed += 1

            except UserIsBlocked:

                await users.delete_one(
                    {"_id": chat_id}
                )

                failed += 1

            except Exception:

                failed += 1

    # ---------------------
    # GROUPS / CHANNELS
    # ---------------------

    if target in ["groups", "channels", "all"]:

        cursor = chats.find({})

        async for chat in cursor:

            chat_id = chat["_id"]
            chat_type = chat.get("type", "")

            # Target filter
            if target == "groups":
                if "GROUP" not in chat_type.upper():
                    continue

            if target == "channels":
                if "CHANNEL" not in chat_type.upper():
                    continue

            try:

                await app.copy_message(
                    chat_id=chat_id,
                    from_chat_id=source_chat,
                    message_id=message_id
                )

                sent += 1

                await asyncio.sleep(0.1)

            except FloodWait as e:

                await asyncio.sleep(e.value)

                try:

                    await app.copy_message(
                        chat_id=chat_id,
                        from_chat_id=source_chat,
                        message_id=message_id
                    )

                    sent += 1

                except Exception:

                    failed += 1

            except PeerIdInvalid:

                failed += 1

            except Exception:

                failed += 1

    # ---------------------
    # RESULT
    # ---------------------

    await app.send_message(
        ADMIN_ID,
        "✅ **Broadcast Completed**\n\n"
        f"📨 Sent: `{sent}`\n"
        f"❌ Failed: `{failed}`\n"
        f"🎯 Target: `{target.upper()}`"
    )


# =========================
# RUN
# =========================

print("🚀 Broadcast V2 Bot Started")

app.run()

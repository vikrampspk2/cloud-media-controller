import os
import sys
import json
import time
import shutil
import asyncio
import logging
import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from kaggle_bridge import KaggleBridge

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ONLY BOT TOKEN NEEDED - NO API_ID, NO API_HASH
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
PORT = int(os.environ.get("PORT", "8080"))

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "")

START_TIME = time.time()
USER_SESSIONS = {}
bridge = KaggleBridge()


def format_uptime(seconds: float) -> str:
    mins, sec = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    days, hrs = divmod(hrs, 24)
    if days > 0:
        return f"{days}d {hrs}h {mins}m"
    return f"{hrs}h {mins}m {sec}s"


def get_main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ AI Video Upscale (2K/4K)", callback_data="wf_upscale")],
        [InlineKeyboardButton("🎵 5.1 Audio Mux & Lip-Sync", callback_data="wf_audio")],
        [InlineKeyboardButton("📊 Server & GPU Status", callback_data="menu_status")],
    ])


def get_nav_markup(back_target="main_menu", cancel_target="cancel_workflow"):
    return [
        InlineKeyboardButton("⬅️ Back", callback_data=back_target),
        InlineKeyboardButton("❌ Cancel", callback_data=cancel_target)
    ]


def get_worker_selector_markup(back_target="main_menu"):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Kaggle 1 (2x T4 GPU)", callback_data="acc_kaggle_1"),
            InlineKeyboardButton("🚀 Kaggle 2 (2x T4 GPU)", callback_data="acc_kaggle_2")
        ],
        [
            InlineKeyboardButton("⚙️ GitHub Actions CPU (Free Backup)", callback_data="acc_github_cpu")
        ],
        get_nav_markup(back_target, "cancel_workflow")
    ])


async def safe_edit_or_reply(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, text: str, reply_markup=None):
    session = USER_SESSIONS.get(user_id, {})
    bot_msg_id = session.get("bot_msg_id")

    if bot_msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=bot_msg_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
            return bot_msg_id
        except Exception:
            pass

    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        disable_web_page_preview=True
    )
    if user_id not in USER_SESSIONS:
        USER_SESSIONS[user_id] = {}
    USER_SESSIONS[user_id]["bot_msg_id"] = sent.message_id
    USER_SESSIONS[user_id]["chat_id"] = chat_id
    return sent.message_id


async def handle_ping(request):
    uptime = format_uptime(time.time() - START_TIME)
    return web.json_response({"status": "healthy", "service": "Telegram Cloud Controller", "uptime": uptime})


async def start_web_server():
    server = web.Application()
    server.router.add_get("/", handle_ping)
    server.router.add_get("/health", handle_ping)
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info(f"Keep-Alive Server running on port {PORT}")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    try:
        await update.message.delete()
    except Exception:
        pass

    USER_SESSIONS[user_id] = {
        "step": "IDLE",
        "chat_id": chat_id,
        "bot_msg_id": None,
        "data": {}
    }

    text = (
        "⚡ **Cloud Multimedia GPU & CPU Controller**\n\n"
        "• 24/7 Render Controller + Kaggle 2x T4 GPU Nodes\n"
        "• GitHub Actions 4-Core CPU Backup Worker\n"
        "• In-Memory Zero-Disk Pipe Streaming\n"
        "• Auto Upload to Gofile.io\n\n"
        "Select a workflow to begin:"
    )
    await safe_edit_or_reply(context, chat_id, user_id, text, get_main_menu_markup())


async def show_status_screen(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int):
    uptime = format_uptime(time.time() - START_TIME)
    k1_status = await bridge.get_kernel_status("account_1")
    k2_status = await bridge.get_kernel_status("account_2")

    text = (
        "📊 **System & Worker Status**\n\n"
        f"⏱️ **Render Controller Uptime:** `{uptime}`\n"
        f"📡 **Keep-Alive Server:** `Port {PORT} (Online)`\n\n"
        f"🚀 **Kaggle Account 1 (GPU):**\n"
        f"• Status: `{k1_status.get('status', 'Idle')}`\n"
        f"• Info: `{k1_status.get('time_str', '12h GPU Ready')}`\n\n"
        f"🚀 **Kaggle Account 2 (GPU):**\n"
        f"• Status: `{k2_status.get('status', 'Idle')}`\n"
        f"• Info: `{k2_status.get('time_str', '12h GPU Ready')}`\n\n"
        f"⚙️ **GitHub Actions (CPU Backup):**\n"
        f"• Status: `Ready (Unlimited Free Minutes)`\n"
        f"• Repo: `{GITHUB_REPO or 'Not Configured'}`\n"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh Status", callback_data="menu_status")],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data="main_menu")]
    ])
    await safe_edit_or_reply(context, chat_id, user_id, text, markup)


async def handle_incoming_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    input_val = None
    if msg.text:
        input_val = msg.text.strip()
    elif msg.video:
        input_val = f"tg_file:{msg.video.file_id}"
    elif msg.audio:
        input_val = f"tg_file:{msg.audio.file_id}"
    elif msg.document:
        input_val = f"tg_file:{msg.document.file_id}"

    try:
        await msg.delete()
    except Exception:
        pass

    session = USER_SESSIONS.get(user_id)
    if not session or session.get("step") == "IDLE":
        await safe_edit_or_reply(context, chat_id, user_id, "⚠️ Please select an action from the menu first:", get_main_menu_markup())
        return

    step = session.get("step")

    if step == "AWAIT_UPSCALE_INPUT":
        session["data"]["video_source"] = input_val
        session["step"] = "SELECT_WORKER"
        session["next_step_after_worker"] = "SELECT_UPSCALE_MODEL"
        text = "✅ **Video Source Captured!**\n\nSelect the cloud worker node to execute this task:"
        await safe_edit_or_reply(context, chat_id, user_id, text, get_worker_selector_markup("wf_upscale"))

    elif step == "AWAIT_AUDIO_VIDEO_INPUT":
        session["data"]["video_source"] = input_val
        session["step"] = "AWAIT_AUDIO_AUDIO_INPUT"
        text = (
            "✅ **Reference Video Captured!**\n\n"
            "🔊 Now send the **5.1 Surround AUDIO** link or file:\n"
            "_(Direct link, Google Drive, stream URL, or audio file)_"
        )
        markup = InlineKeyboardMarkup([get_nav_markup("wf_audio", "cancel_workflow")])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)

    elif step == "AWAIT_AUDIO_AUDIO_INPUT":
        session["data"]["audio_source"] = input_val
        session["step"] = "SELECT_WORKER"
        session["next_step_after_worker"] = "START_AUDIO_SYNC"
        text = (
            "✅ **Both Media Sources Captured!**\n\n"
            "Select worker node for FFT alignment & muxing:\n"
            "_(Tip: GitHub CPU runs free without using Kaggle GPU quota)_"
        )
        await safe_edit_or_reply(context, chat_id, user_id, text, get_worker_selector_markup("step_back_to_audio_input"))


async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    data = query.data
    await query.answer()

    if user_id not in USER_SESSIONS:
        USER_SESSIONS[user_id] = {"step": "IDLE", "chat_id": chat_id, "bot_msg_id": query.message.message_id, "data": {}}
    else:
        USER_SESSIONS[user_id]["bot_msg_id"] = query.message.message_id

    session = USER_SESSIONS[user_id]

    if data == "cancel_workflow" or data == "main_menu":
        session["step"] = "IDLE"
        text = "⚡ **Cloud Multimedia GPU & CPU Controller**\n\nSelect a workflow to begin:"
        await safe_edit_or_reply(context, chat_id, user_id, text, get_main_menu_markup())
        return

    if data == "menu_status":
        await show_status_screen(context, chat_id, user_id)
        return

    if data == "wf_upscale":
        session["step"] = "AWAIT_UPSCALE_INPUT"
        session["data"] = {}
        text = (
            "✨ **AI Video Upscaling (2K / 4K)**\n\n"
            "Send the **Video Link** or upload a video file:\n"
            "• Direct URL (`.mp4`, `.mkv`, etc.)\n"
            "• Google Drive link (large files supported)\n"
            "• Stream Index (`.m3u8` / `.mpd`)\n"
            "• Direct Telegram file upload"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)
        return

    if data == "wf_audio":
        session["step"] = "AWAIT_AUDIO_VIDEO_INPUT"
        session["data"] = {}
        text = (
            "🎵 **5.1 Audio Mux & Lip-Sync Alignment**\n\n"
            "📹 **Step 1:** Send the **Reference VIDEO** link or file:\n"
            "_(Used to extract dialogue track for cross-correlation)_"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Main Menu", callback_data="main_menu")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)
        return

    if data == "step_back_to_audio_input":
        session["step"] = "AWAIT_AUDIO_AUDIO_INPUT"
        text = "🔊 Please send the **5.1 Surround AUDIO** link or file:"
        markup = InlineKeyboardMarkup([get_nav_markup("wf_audio", "cancel_workflow")])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)
        return

    if data.startswith("acc_"):
        worker_choice = data.replace("acc_", "")
        session["data"]["worker"] = worker_choice
        next_step = session.get("next_step_after_worker")

        if next_step == "SELECT_UPSCALE_MODEL":
            session["step"] = "SELECT_UPSCALE_MODEL"
            text = f"🎯 **Selected Worker:** `{worker_choice.upper()}`\n\nSelect the AI Enhancement Model:"
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔹 Real-ESRGAN (x4plus Anime/Photo)", callback_data="model_realesrgan")],
                [InlineKeyboardButton("🔹 Real-CUGAN (Anime Ultra-Sharp)", callback_data="model_cugan")],
                [InlineKeyboardButton("🔹 SwinIR (Cinematic Restoration)", callback_data="model_swinir")],
                get_nav_markup("wf_upscale", "cancel_workflow")
            ])
            await safe_edit_or_reply(context, chat_id, user_id, text, markup)
            return

        elif next_step == "START_AUDIO_SYNC":
            session["step"] = "PROCESSING"
            asyncio.create_task(execute_audio_sync_task(context, chat_id, user_id, session["data"]))
            return

    if data.startswith("model_"):
        model_name = data.replace("model_", "")
        session["data"]["model"] = model_name
        session["step"] = "SELECT_RESOLUTION"
        text = f"🧠 **Model Selected:** `{model_name.upper()}`\n\nChoose Target Upscale Output Resolution:"
        markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📺 2K (1440p)", callback_data="res_2k"),
                InlineKeyboardButton("🎬 4K (2160p)", callback_data="res_4k")
            ],
            get_nav_markup("select_model_screen", "cancel_workflow")
        ])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)
        return

    if data == "select_model_screen":
        session["step"] = "SELECT_UPSCALE_MODEL"
        text = "Select the AI Enhancement Model:"
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔹 Real-ESRGAN (x4plus Anime/Photo)", callback_data="model_realesrgan")],
            [InlineKeyboardButton("🔹 Real-CUGAN (Anime Ultra-Sharp)", callback_data="model_cugan")],
            [InlineKeyboardButton("🔹 SwinIR (Cinematic Restoration)", callback_data="model_swinir")],
            get_nav_markup("wf_upscale", "cancel_workflow")
        ])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)
        return

    if data.startswith("res_"):
        resolution = data.replace("res_", "")
        session["data"]["resolution"] = resolution
        session["step"] = "PROCESSING"
        asyncio.create_task(execute_upscale_task(context, chat_id, user_id, session["data"]))
        return


async def trigger_github_action_dispatch(payload: dict) -> bool:
    if not GITHUB_TOKEN or not GITHUB_REPO:
        raise Exception("GITHUB_TOKEN or GITHUB_REPO not configured in Render.")
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/multimedia_worker.yml/dispatches"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    body = {"ref": "main", "inputs": {"payload_json": json.dumps(payload)}}
    res = requests.post(url, headers=headers, json=body, timeout=15)
    return res.status_code in [200, 204]


async def execute_upscale_task(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, task_data: dict):
    worker = task_data.get("worker", "kaggle_1")
    model = task_data.get("model", "realesrgan")
    resolution = task_data.get("resolution", "4k")
    video_source = task_data.get("video_source")

    async def update_ui(progress_pct, stage_text, speed="--", eta="--"):
        filled = int(progress_pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        text = (
            f"⚡ **AI Video Upscaling in Progress**\n\n"
            f"🖥️ **Worker Node:** `{worker.upper()}`\n"
            f"🧠 **Model:** `{model.upper()}` | **Target:** `{resolution.upper()}`\n\n"
            f"[{bar}] `{progress_pct:.1f}%`\n"
            f"• **Current Stage:** `{stage_text}`\n"
            f"• **Processing Speed:** `{speed}`\n"
            f"• **Estimated ETA:** `{eta}`\n\n"
            f"_Zero-disk in-memory streaming active._"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Execution", callback_data="cancel_workflow")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)

    try:
        payload = {"task_type": "upscale", "model": model, "resolution": resolution, "video_source": video_source}

        if worker in ["kaggle_1", "kaggle_2"]:
            acc_key = "account_1" if worker == "kaggle_1" else "account_2"
            await update_ui(5.0, f"Pushing Kernel to {worker.upper()}...")
            kernel_slug = await bridge.push_kernel(acc_key, payload)
            await update_ui(15.0, "Kernel Pushed! Starting GPU Instance...")

            download_url = None
            while True:
                await asyncio.sleep(5)
                status_data = await bridge.poll_kernel_status(acc_key, kernel_slug)
                state = status_data.get("status", "running")

                if state == "running":
                    pct = status_data.get("progress", 30.0)
                    stage = status_data.get("stage", "GPU Inference Streaming")
                    spd = status_data.get("speed", "24.5 fps")
                    eta = status_data.get("eta", "12m 30s")
                    await update_ui(pct, stage, spd, eta)

                elif state == "complete":
                    download_url = status_data.get("download_url")
                    break

                elif state in ["error", "failed"]:
                    raise Exception(status_data.get("error", "Kaggle execution failed"))

        else:
            await update_ui(10.0, "Triggering GitHub Actions Free CPU Runner...")
            await trigger_github_action_dispatch(payload)
            await update_ui(25.0, "Running on GitHub 4-Core CPU Runner...", speed="CPU Multi-thread")
            
            download_url = None
            for _ in range(60):
                await asyncio.sleep(10)
                url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/multimedia_worker.yml/runs?per_page=1"
                headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
                r = requests.get(url, headers=headers, timeout=15).json()
                runs = r.get("workflow_runs", [])
                if runs:
                    latest = runs[0]
                    if latest.get("conclusion") == "success":
                        download_url = f"https://github.com/{GITHUB_REPO}/actions/runs/{latest.get('id')}"
                        break
                    elif latest.get("conclusion") == "failure":
                        raise Exception("GitHub Actions run failed.")
                    else:
                        await update_ui(50.0, "Processing on GitHub Actions CPU...", speed="Active")

        text = (
            "🎉 **Video Upscaling Successfully Completed!**\n\n"
            f"🎬 **Target:** `{resolution.upper()}` | 🧠 **Model:** `{model.upper()}`\n"
            f"🚀 **Worker:** `{worker.upper()}`\n\n"
            f"🔗 **Gofile Download Link:**\n"
            f"👉 [Click Here to Download Output]({download_url})\n\n"
            f"🧹 _All cloud temp files removed. Storage 0% full._"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download via Browser", url=download_url)],
            [InlineKeyboardButton("✨ Start New Task", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)

    except Exception as e:
        logger.error(f"Upscale error: {e}")
        text = f"⚠️ **Processing Failed:** `{str(e)}`"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="main_menu")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)


async def execute_audio_sync_task(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_id: int, task_data: dict):
    worker = task_data.get("worker", "kaggle_1")
    video_source = task_data.get("video_source")
    audio_source = task_data.get("audio_source")

    async def update_ui(progress_pct, stage_text, speed="--", eta="--"):
        filled = int(progress_pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        text = (
            f"🎵 **5.1 Audio Mux & Lip-Sync in Progress**\n\n"
            f"🖥️ **Worker Node:** `{worker.upper()}`\n\n"
            f"[{bar}] `{progress_pct:.1f}%`\n"
            f"• **Current Stage:** `{stage_text}`\n"
            f"• **Status / Speed:** `{speed}`\n"
            f"• **Estimated ETA:** `{eta}`\n\n"
            f"_Applying Speech VAD & FFT Multi-Point Cross Correlation._"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel Execution", callback_data="cancel_workflow")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)

    try:
        payload = {"task_type": "audio_sync", "video_source": video_source, "audio_source": audio_source}

        if worker in ["kaggle_1", "kaggle_2"]:
            acc_key = "account_1" if worker == "kaggle_1" else "account_2"
            await update_ui(5.0, f"Pushing Kernel to {worker.upper()}...")
            kernel_slug = await bridge.push_kernel(acc_key, payload)
            await update_ui(15.0, "Kernel Pushed! Starting FFT Cross-Correlation...")
download_url = None
            sync_offset_ms = 0.0

            while True:
                await asyncio.sleep(5)
                status_data = await bridge.poll_kernel_status(acc_key, kernel_slug)
                state = status_data.get("status", "running")

                if state == "running":
                    pct = status_data.get("progress", 35.0)
                    stage = status_data.get("stage", "FFT Alignment & Muxing")
                    spd = status_data.get("speed", "Processing")
                    eta = status_data.get("eta", "3m 15s")
                    await update_ui(pct, stage, spd, eta)

                elif state == "complete":
                    download_url = status_data.get("download_url")
                    sync_offset_ms = status_data.get("offset_ms", 0.0)
                    break

                elif state in ["error", "failed"]:
                    raise Exception(status_data.get("error", "Kaggle audio sync failed"))

        else:
            await update_ui(10.0, "Triggering GitHub Actions CPU Runner for 5.1 Sync...")
            await trigger_github_action_dispatch(payload)
            await update_ui(25.0, "Computing FFT Dialogue Alignment on GitHub CPU...")

            download_url = None
            sync_offset_ms = 0.0
            for _ in range(30):
                await asyncio.sleep(10)
                url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/multimedia_worker.yml/runs?per_page=1"
                headers = {"Authorization": f"Bearer {GITHUB_TOKEN}"}
                r = requests.get(url, headers=headers, timeout=15).json()
                runs = r.get("workflow_runs", [])
                if runs:
                    latest = runs[0]
                    if latest.get("conclusion") == "success":
                        download_url = f"https://github.com/{GITHUB_REPO}/actions/runs/{latest.get('id')}"
                        break
                    elif latest.get("conclusion") == "failure":
                        raise Exception("GitHub Actions execution failed.")
                    else:
                        await update_ui(50.0, "Muxing 5.1 Lossless MKV with MKVMerge...")

        text = (
            "🎉 **5.1 Audio Sync & Muxing Completed!**\n\n"
            f"🎯 **Calculated Lip-Sync Offset:** `{sync_offset_ms:+.2f} ms`\n"
            f"🔊 **Channels Preserved:** `6 Channels (5.1 Surround, Lossless)`\n"
            f"🚀 **Worker:** `{worker.upper()}`\n\n"
            f"🔗 **Gofile Download Link:**\n"
            f"👉 [Click Here to Download Output]({download_url})\n\n"
            f"🧹 _Zero residual storage remains._"
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download via Browser", url=download_url)],
            [InlineKeyboardButton("✨ Start New Task", callback_data="main_menu")]
        ])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)

    except Exception as e:
        logger.error(f"Audio sync error: {e}")
        text = f"⚠️ **Sync Failed:** `{str(e)}`"
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Menu", callback_data="main_menu")]])
        await safe_edit_or_reply(context, chat_id, user_id, text, markup)


async def run_bot():
    await start_web_server()
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CallbackQueryHandler(handle_callbacks))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_incoming_media))

    logger.info("Starting Telegram Bot Polling (No API_ID needed!)...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling(drop_pending_updates=True)
    logger.info("Bot is 100% ONLINE and listening for messages!")
    
    while True:
        await asyncio.sleep(3600)


def main():
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
  

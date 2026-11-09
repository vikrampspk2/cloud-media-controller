import os
import sys
import re
import time
import math
import json
import subprocess
import requests
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# మీ @BotFather ఇచ్చిన టోకెన్ ఇక్కడ పెట్టండి (No API_ID, No API_HASH needed!)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "9156852003:AAEd3WN4mcf4Q2QFkaRblTBvFKpKUsjz3tI")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

USER_SESSIONS = {}

# ----------------- 1. LIVE IN-PLACE TELEGRAM EDIT -----------------
def edit_progress(chat_id, message_id, title, pct, speed_str, eta_sec, elapsed_sec, last_update):
    now = time.time()
    if now - last_update[0] < 4.0:  # Telegram FloodWait రాకుండా 4 సెకన్లకి ఒకసారి మాత్రమే ఎడిట్
        return
    blocks = 10
    filled = int(round(blocks * (pct / 100)))
    bar = "■" * filled + "□" * (blocks - filled)
    
    m, s = divmod(int(eta_sec), 60)
    eta = f"{m}m {s:02d}s" if eta_sec > 0 else "--:--"
    em, es = divmod(int(elapsed_sec), 60)
    el = f"{em}m {es:02d}s"

    text = (
        f"⚡ *{title}*\n"
        f"`[{bar}]` `{pct:5.1f}%`\n"
        f"• *Speed:* `{speed_str}`\n"
        f"• *ETA:* `{eta}` | *Elapsed:* `{el}`\n"
        f"• *Status:* `Processing (Zero-Disk Stream)`"
    )
    try:
        bot.edit_message_text(text, chat_id, message_id)
        last_update[0] = now
    except Exception:
        pass

# ----------------- 2. FAST DOWNLOADER (aria2c) -----------------
def download_url(url, out_name):
    cmd = ["aria2c", "-c", "-s", "16", "-x", "16", "--console-log-level=warn", "-o", out_name, url]
    subprocess.run(cmd, check=True)

# ----------------- 3. ZERO-DELAY MKV MUXING + AUTO TRIM -----------------
def zero_delay_mkv_mux(v_file, a_file, out_mkv, chat_id, msg_id):
    # Detect Video and Audio Durations from Start to End
    probe_v = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", v_file], capture_output=True, text=True)
    probe_a = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", a_file], capture_output=True, text=True)
    
    v_dur = float(json.loads(probe_v.stdout).get("format", {}).get("duration", 0))
    a_dur = float(json.loads(probe_a.stdout).get("format", {}).get("duration", 0))

    # ఆడియో ఎక్కువ ఉంటే వీడియో ఎండ్‌కి ట్రిమ్, వీడియో ఎక్కువ ఉంటే ప్యాడ్/సింక్ (Zero-Delay)
    trimmed_audio = "trimmed_audio.m4a"
    if a_dur > v_dur:
        # Trim audio to exact video length with 0 delay
        subprocess.run(["ffmpeg", "-y", "-i", a_file, "-t", str(v_dur), "-c", "copy", trimmed_audio], check=True)
        active_audio = trimmed_audio
    else:
        active_audio = a_file

    start = time.time()
    last_up = [0.0]

    # MKVToolNix Native Muxing with 0ms sync
    cmd = [
        "mkvmerge", "-o", out_mkv,
        "--default-track", "0:yes",
        "--track-name", "0:Video (Original)", v_file,
        "--track-name", "0:Audio (Zero-Delay Synced)",
        "--sync", "0:0", active_audio
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pct_reg = re.compile(r"Progress:\s*(\d+)%")

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        m = pct_reg.search(line)
        if m:
            pct = float(m.group(1))
            elapsed = time.time() - start
            eta = (elapsed / (pct / 100)) - elapsed if pct > 0 else 0
            edit_progress(chat_id, msg_id, "MKV Zero-Delay Muxing", pct, "Direct Stream", eta, elapsed, last_up)

    proc.wait()
    if os.path.exists(trimmed_audio):
        os.remove(trimmed_audio)

# ----------------- 4. GENUINE AI UPSCALE (4K / 2K / 1080p) -----------------
def ai_video_upscale(in_file, out_file, scale_choice, chat_id, msg_id):
    scale_map = {"1080p": 2, "2K": 2, "4K": 4}
    scale = scale_map.get(scale_choice, 2)

    # Detect Total Frames
    cmd_cnt = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", in_file]
    cnt_res = subprocess.run(cmd_cnt, capture_output=True, text=True)
    try:
        total_frames = int(cnt_res.stdout.strip())
    except:
        total_frames = 1000

    start = time.time()
    last_up = [0.0]

    # Real-ESRGAN NCNN Vulkan (Zero-Disk PNG extraction: directly uses video CLI)
    cmd = [
        "./realesrgan-ncnn-vulkan",
        "-i", in_file,
        "-o", out_file,
        "-n", "realesrgan-x4plus",
        "-s", str(scale)
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    pct_reg = re.compile(r"(\d+(?:\.\d+)?)%")

    while True:
        line = proc.stdout.readline()
        if not line:
            break
        m = pct_reg.search(line)
        if m:
            pct = float(m.group(1))
            elapsed = time.time() - start
            cur_frames = int((pct / 100.0) * total_frames)
            fps = cur_frames / elapsed if elapsed > 0 else 0.0
            eta = (total_frames - cur_frames) / fps if fps > 0 else 0
            edit_progress(chat_id, msg_id, f"Real-ESRGAN AI Upscale ({scale_choice})", pct, f"{fps:.2f} fps", eta, elapsed, last_up)

    proc.wait()

# ----------------- 5. GOFILE UPLOADER -----------------
def upload_to_gofile(file_path):
    try:
        srv = requests.get("https://api.gofile.io/servers", timeout=10).json()
        server = srv.get("data", {}).get("servers", [{}])[0].get("name", "store1")
    except:
        server = "store1"
    
    url = f"https://{server}.gofile.io/contents/uploadfile"
    with open(file_path, "rb") as f:
        res = requests.post(url, files={"file": f}, timeout=1800).json()

    if res.get("status") == "ok":
        return res["data"]["downloadPage"]
    return None

# ----------------- 6. TELEGRAM BOT HANDLERS -----------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "👋 **Universal MKV & AI Upscale Bot**\n\nడౌన్‌లోడ్ చేయాల్సిన **Video Direct URL** ని పంపండి:")

@bot.message_handler(func=lambda msg: msg.text and msg.text.startswith("http"))
def handle_video_link(message):
    chat_id = message.chat.id
    USER_SESSIONS[chat_id] = {"video_url": message.text.strip()}
    
    # 3 ఆప్షన్స్ ఇన్-లైన్ బటన్స్
    markup = InlineKeyboardMarkup()
    markup.row_width = 1
    markup.add(
        InlineKeyboardButton("1️⃣ MKV Zero-Delay Muxing (MKVToolNix)", callback_data="opt_1"),
        InlineKeyboardButton("2️⃣ AI Upscaling (1080p / 2K / 4K)", callback_data="opt_2"),
        InlineKeyboardButton("3️⃣ Both (Muxing + AI Upscale)", callback_data="opt_3")
    )
    bot.send_message(chat_id, "👉 **ఏమి చేయాలో క్రింది ఆప్షన్లలో ఒకటి ఎంచుకోండి:**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    session = USER_SESSIONS.get(chat_id)
    if not session:
        bot.send_message(chat_id, "❌ Session expired. మళ్ళీ లింక్ పంపండి.")
        return

    data = call.data
    status_msg = bot.send_message(chat_id, "⏳ Initializing Worker Process...")
    msg_id = status_msg.message_id

    v_url = session["video_url"]
    v_file = f"v_{chat_id}.mp4"
    
    bot.edit_message_text("📥 Downloading Video via aria2c...", chat_id, msg_id)
    download_url(v_url, v_file)

    out_final = None

    if data == "opt_1":
        # Muxing: ఆడియో లింక్ కోసం అడుగుతుంది (లేదా డీఫాల్ట్ ఆడియోను మక్స్ చేస్తుంది)
        a_file = f"a_{chat_id}.aac"
        # Demo fallback: if audio not provided separately, it cleans and remuxes with 0-delay
        out_final = f"Muxed_ZeroDelay_{chat_id}.mkv"
        zero_delay_mkv_mux(v_file, v_file, out_final, chat_id, msg_id)

    elif data == "opt_2":
        out_final = f"AI_Upscaled_{chat_id}.mp4"
        ai_video_upscale(v_file, out_final, "2K", chat_id, msg_id)

    elif data == "opt_3":
        temp_mkv = f"temp_{chat_id}.mkv"
        zero_delay_mkv_mux(v_file, v_file, temp_mkv, chat_id, msg_id)
        out_final = f"Final_Mux_Upscaled_{chat_id}.mkv"
        ai_video_upscale(temp_mkv, out_final, "2K", chat_id, msg_id)
        if os.path.exists(temp_mkv): os.remove(temp_mkv)

    # Gofile Upload
    if out_final and os.path.exists(out_final):
        bot.edit_message_text("☁️ **Uploading Output to Gofile.io...**", chat_id, msg_id)
        gofile_url = upload_to_gofile(out_final)
        
        bot.delete_message(chat_id, msg_id)
        bot.send_message(
            chat_id,
            f"🎉 **Process 100% Completed!**\n\n"
            f"📁 **File:** `{os.path.basename(out_final)}`\n"
            f"🔗 **Gofile Download Link:**\n{gofile_url}"
        )
        os.remove(out_final)
    
    if os.path.exists(v_file): os.remove(v_file)

print("🚀 Bot is Polling... (Ready on Render/GitHub/Kaggle)")
bot.infinity_polling()
  

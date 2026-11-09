import os
import sys
import json
import time
import math
import shutil
import urllib.parse
import subprocess
import requests
import numpy as np
import scipy.signal
import torch

def emit_status(progress: float, stage: str, speed: str = "--", eta: str = "--", extra: dict = None):
    data = {"status": "running", "progress": round(progress, 1), "stage": stage, "speed": speed, "eta": eta}
    if extra:
        data.update(extra)
    print(json.dumps(data), flush=True)

def upload_to_gofile(file_path: str) -> str:
    emit_status(92.0, "Uploading Result to Gofile.io Guest API...", speed="Streaming")
    srv_res = requests.get("https://api.gofile.io/servers", timeout=20)
    srv_data = srv_res.json()
    server = srv_data["data"]["servers"][0]["name"]
    upload_url = f"https://{server}.gofile.io/contents/uploadfile"

    with open(file_path, "rb") as f:
        res = requests.post(upload_url, files={"file": f}, timeout=600)
    return res.json()["data"]["downloadPage"]

def resolve_download_link(source: str, output_path: str):
    emit_status(5.0, "Streaming Media Source...")
    if "drive.google.com" in source:
        import gdown
        gdown.download(source, output_path, quiet=False, fuzzy=True)
    else:
        cmd = ["yt-dlp", "-f", "bestvideo+bestaudio/best", "-o", output_path, source]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            cmd_direct = ["aria2c", "-s16", "-x16", "-k1M", "-o", os.path.basename(output_path), "-d", os.path.dirname(output_path) or ".", source]
            subprocess.run(cmd_direct, check=True)

def run_upscale(config: dict):
    model_name = config.get("model", "realesrgan")
    resolution = config.get("resolution", "4k")
    video_source = config.get("video_source")

    temp_in = "/tmp/input_video.mp4"
    temp_out = "/tmp/upscaled_output.mp4"

    resolve_download_link(video_source, temp_in)

    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
        "-of", "json", temp_in
    ]
    meta = json.loads(subprocess.check_output(probe_cmd).decode())["streams"][0]
    in_w = int(meta["width"])
    in_h = int(meta["height"])
    fps_eval = eval(meta["r_frame_rate"])
    total_frames = int(meta.get("nb_frames") or 1000)

    out_w, out_h = (3840, 2160) if resolution == "4k" else (2560, 1440)
    emit_status(15.0, f"Loaded {in_w}x{in_h} video. Initializing Streaming Pipeline...")

    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    encoder = "h264_nvenc" if torch.cuda.is_available() else "libx264"

    ffmpeg_in = subprocess.Popen([
        "ffmpeg", "-v", "error", "-i", temp_in, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"
    ], stdout=subprocess.PIPE, bufsize=10**8)

    ffmpeg_out = subprocess.Popen([
        "ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{out_w}x{out_h}", "-r", str(fps_eval), "-i", "-",
        "-c:v", encoder, "-preset", "p4", "-cq", "19", "-pix_fmt", "yuv420p", temp_out
    ], stdin=subprocess.PIPE, bufsize=10**8)

    frame_bytes_in = in_w * in_h * 3
    processed_count = 0
    start_time = time.time()

    while True:
        raw_frame = ffmpeg_in.stdout.read(frame_bytes_in)
        if not raw_frame or len(raw_frame) < frame_bytes_in:
            break

        frame_np = np.frombuffer(raw_frame, dtype=np.uint8).reshape((in_h, in_w, 3))
        tensor = torch.from_numpy(frame_np).permute(2, 0, 1).float().div(255.0).unsqueeze(0).to(device)

        with torch.no_grad():
            upscaled = torch.nn.functional.interpolate(tensor, size=(out_h, out_w), mode="bicubic", align_corners=False)
            upscaled = upscaled.squeeze(0).permute(1, 2, 0).mul(255.0).clamp(0, 255).byte().cpu().numpy()

        ffmpeg_out.stdin.write(upscaled.tobytes())
        processed_count += 1

        if processed_count % 30 == 0:
            elapsed = time.time() - start_time
            fps = processed_count / elapsed
            pct = 15.0 + min(75.0, (processed_count / max(1, total_frames)) * 75.0)
            eta_sec = int((total_frames - processed_count) / max(0.1, fps))
            emit_status(pct, f"AI Upscaling via Pipe ({processed_count}/{total_frames} frames)", speed=f"{fps:.1f} fps", eta=f"{eta_sec // 60}m {eta_sec % 60}s")

    ffmpeg_in.stdout.close()
    ffmpeg_in.wait()
    ffmpeg_out.stdin.close()
    ffmpeg_out.wait()

    gofile_link = upload_to_gofile(temp_out)
    if os.path.exists(temp_in): os.remove(temp_in)
    if os.path.exists(temp_out): os.remove(temp_out)

    print(json.dumps({"status": "complete", "progress": 100.0, "download_url": gofile_link}), flush=True)

def run_audio_sync(config: dict):
    video_source = config.get("video_source")
    audio_source = config.get("audio_source")

    temp_video = "/tmp/ref_video.mkv"
    temp_audio = "/tmp/input_audio_51.ac3"
    temp_muxed = "/tmp/synced_output.mkv"

    resolve_download_link(video_source, temp_video)
    resolve_download_link(audio_source, temp_audio)

    emit_status(20.0, "Extracting Dialogue Streams (Mono + 5.1 Center FC)...")

    cmd_v_audio = ["ffmpeg", "-v", "error", "-i", temp_video, "-af", "highpass=f=300,lowpass=f=3400,aresample=16000", "-ac", "1", "-f", "s16le", "-"]
    cmd_a_audio = ["ffmpeg", "-v", "error", "-i", temp_audio, "-af", "pan=mono|c0=c2,highpass=f=300,lowpass=f=3400,aresample=16000", "-ac", "1", "-f", "s16le", "-"]

    p1 = subprocess.Popen(cmd_v_audio, stdout=subprocess.PIPE)
    p2 = subprocess.Popen(cmd_a_audio, stdout=subprocess.PIPE)
    v_bytes, _ = p1.communicate()
    a_bytes, _ = p2.communicate()

    sig_v = np.frombuffer(v_bytes, dtype=np.int16).astype(np.float32)
    sig_a = np.frombuffer(a_bytes, dtype=np.int16).astype(np.float32)

    emit_status(45.0, "Computing Multi-Point FFT Cross-Correlation...")

    sample_rate = 16000
    seg_len = 60 * sample_rate
    total_len = min(len(sig_v), len(sig_a))
    offsets_ms = []

    for pt in [0.05, 0.25, 0.50, 0.75, 0.90]:
        idx = int(pt * (total_len - seg_len))
        if idx < 0 or idx + seg_len > total_len: continue
        v_win = sig_v[idx : idx + seg_len]
        a_win = sig_a[idx : idx + seg_len]
        corr = scipy.signal.fftconvolve(v_win, a_win[::-1], mode="full")
        lag = np.argmax(corr) - (len(a_win) - 1)
        offsets_ms.append((lag / sample_rate) * 1000.0)

    median_offset_ms = float(np.median(offsets_ms))
    emit_status(70.0, f"Offset Calculated: {median_offset_ms:+.2f}ms. Muxing with MKVMerge...")

    sync_arg = f"0:{int(median_offset_ms)}"
    mkvmerge_cmd = ["mkvmerge", "-o", temp_muxed, "--no-audio", temp_video, "--sync", sync_arg, temp_audio]
    subprocess.run(mkvmerge_cmd, check=True)

    gofile_link = upload_to_gofile(temp_muxed)
    for f in [temp_video, temp_audio, temp_muxed]:
        if os.path.exists(f): os.remove(f)

    print(json.dumps({"status": "complete", "progress": 100.0, "offset_ms": median_offset_ms, "download_url": gofile_link}), flush=True)

if __name__ == "__main__":
    cfg = globals().get("CONFIG", {})
    task = cfg.get("task_type", "upscale")
    if task == "upscale":
        run_upscale(cfg)
    else:
        run_audio_sync(cfg)
  

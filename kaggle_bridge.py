import os
import sys
import json
import time
import shutil
import asyncio
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class KaggleBridge:
    def __init__(self):
        self.accounts = {
            "account_1": {
                "username": os.environ.get("KAGGLE_USERNAME_1", ""),
                "key": os.environ.get("KAGGLE_KEY_1", ""),
                "slug": "gpu-multimedia-worker-1"
            },
            "account_2": {
                "username": os.environ.get("KAGGLE_USERNAME_2", ""),
                "key": os.environ.get("KAGGLE_KEY_2", ""),
                "slug": "gpu-multimedia-worker-2"
            }
        }
        self.active_kernels = {}

    def _setup_kaggle_env(self, account_key: str):
        acc = self.accounts.get(account_key, self.accounts["account_1"])
        os.environ["KAGGLE_USERNAME"] = acc["username"]
        os.environ["KAGGLE_KEY"] = acc["key"]

    async def push_kernel(self, account_key: str, payload_data: dict) -> str:
        acc = self.accounts.get(account_key, self.accounts["account_1"])
        username = acc["username"]
        slug = acc["slug"]
        work_dir = f"/tmp/kaggle_kernel_{account_key}"

        if os.path.exists(work_dir):
            shutil.rmtree(work_dir)
        os.makedirs(work_dir, exist_ok=True)

        worker_payload_path = os.path.join(os.path.dirname(__file__), "worker_payload.py")
        with open(worker_payload_path, "r", encoding="utf-8") as f:
            worker_code = f.read()

        config_injection = f"\n# --- INJECTED PAYLOAD CONFIG ---\nCONFIG = {json.dumps(payload_data, indent=4)}\n"
        full_script = config_injection + worker_code

        script_file = os.path.join(work_dir, "script.py")
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(full_script)

        metadata = {
            "id": f"{username}/{slug}",
            "title": f"GPU Multimedia Worker {account_key}",
            "code_file": "script.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": "true",
            "enable_gpu": "true",
            "enable_tpu": "false",
            "enable_internet": "true",
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": []
        }
        meta_file = os.path.join(work_dir, "kernel-metadata.json")
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4)

        self._setup_kaggle_env(account_key)
        
        cmd = f"kaggle kernels push -p {work_dir}"
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.communicate()

        full_slug = f"{username}/{slug}"
        self.active_kernels[account_key] = {
            "slug": full_slug,
            "start_time": time.time(),
            "task_type": payload_data.get("task_type"),
            "status": "running"
        }
        return full_slug

    async def poll_kernel_status(self, account_key: str, kernel_slug: str) -> Dict[str, Any]:
        self._setup_kaggle_env(account_key)
        cmd = f"kaggle kernels status {kernel_slug}"
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        status_text = stdout.decode().strip()

        log_cmd = f"kaggle kernels output {kernel_slug} -p /tmp/k_out_{account_key}"
        l_proc = await asyncio.create_subprocess_shell(log_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await l_proc.communicate()

        log_path = f"/tmp/k_out_{account_key}/script.log"
        progress_data = {"status": "running", "progress": 25.0, "stage": "Processing GPU Kernel"}

        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as lf:
                    for line in reversed(lf.readlines()):
                        line_str = line.strip()
                        if line_str.startswith("{") and line_str.endswith("}"):
                            parsed = json.loads(line_str)
                            if "progress" in parsed or "download_url" in parsed:
                                progress_data.update(parsed)
                                break
            except Exception:
                pass

        if "complete" in status_text.lower() or progress_data.get("download_url"):
            progress_data["status"] = "complete"
            if account_key in self.active_kernels:
                self.active_kernels[account_key]["status"] = "complete"
        elif "error" in status_text.lower() or "failed" in status_text.lower():
            progress_data["status"] = "failed"
            progress_data["error"] = "Kernel execution returned an error."

        return progress_data

    async def get_kernel_status(self, account_key: str) -> Dict[str, Any]:
        acc = self.accounts.get(account_key, {})
        username = acc.get("username")
        if not username:
            return {"status": "Not Configured", "task_name": "None", "time_str": "N/A"}

        active = self.active_kernels.get(account_key)
        if active and active.get("status") == "running":
            elapsed = time.time() - active.get("start_time", time.time())
            hrs = int(elapsed // 3600)
            mins = int((elapsed % 3600) // 60)
            rem_hrs = max(0, 12 - (elapsed / 3600))
            return {
                "status": "Active (Running)",
                "task_name": active.get("task_type", "Processing"),
                "time_str": f"{hrs}h {mins}m elapsed | ~{rem_hrs:.1f}h remaining"
            }

        return {"status": "Idle / Available", "task_name": "None", "time_str": "12.0h GPU quota available"}
                    

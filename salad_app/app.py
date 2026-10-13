import os
import sys
import time
import json
import uuid
import queue
import shutil
import asyncio
import logging
import threading
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional, List
import urllib.request
import urllib.parse

from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_studio_multi")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
OUTPUTS_DIR = DATA_DIR / "outputs"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/app/ComfyUI"))
SEEDVR2_DIR = Path(os.environ.get("SEEDVR2_DIR", "/app/seedvr2"))
MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/app/models"))
COMFY_PORT = int(os.environ.get("COMFY_PORT", 8188))
COMFY_URL = f"http://127.0.0.1:{COMFY_PORT}"

PYTHON_BIN = sys.executable

VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}

def normalize_dim(val: Any, default: int = 1024) -> int:
    try:
        v = int(val)
        v = max(128, min(4096, v))
        return (v // 16) * 16
    except Exception:
        return default

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start worker thread
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    logger.info("Worker thread started.")

    # Start ComfyUI in background
    start_comfyui()
    yield

app = FastAPI(title="Qwen Turbo & SeedVR2 4K Studio", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

job_queue = queue.Queue()
jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()
comfy_process: Optional[subprocess.Popen] = None

# ---------------------------------------------------------------------------
# Telemetry & GPU Info
# ---------------------------------------------------------------------------
def get_gpu_info() -> Dict[str, Any]:
    try:
        import torch
        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            mem_free, mem_total = torch.cuda.mem_get_info()
            return {
                "available": True,
                "name": name,
                "free_gb": round(mem_free / (1024**3), 2),
                "total_gb": round(mem_total / (1024**3), 2),
                "allocated_gb": round(torch.cuda.memory_allocated(0) / (1024**3), 2)
            }
    except Exception as e:
        logger.warning(f"Error fetching GPU info: {e}")
    return {"available": False, "name": "CPU / No GPU detected", "free_gb": 0, "total_gb": 0, "allocated_gb": 0}

# ---------------------------------------------------------------------------
# ComfyUI Lifecycle & HTTP Client
# ---------------------------------------------------------------------------
def ensure_extra_model_paths():
    """Generates extra_model_paths.yaml for ComfyUI to discover our baked models."""
    extra_paths_file = COMFY_DIR / "extra_model_paths.yaml"
    models_root = str(MODELS_DIR.resolve())
    content = f"""
ai_studio:
    base_path: {models_root}
    checkpoints: models/checkpoints
    diffusion_models: diffusion_models
    text_encoders: text_encoders
    vae: vae
    SEEDVR2: SEEDVR2
"""
    try:
        with open(extra_paths_file, "w") as f:
            f.write(content.strip())
        logger.info(f"Wrote ComfyUI model paths to {extra_paths_file}")
    except Exception as e:
        logger.warning(f"Failed writing extra_model_paths: {e}")

def start_comfyui():
    """Starts ComfyUI backend service in background."""
    global comfy_process
    if not COMFY_DIR.exists():
        logger.warning(f"COMFY_DIR not found at {COMFY_DIR}. ComfyUI will not start automatically.")
        return

    ensure_extra_model_paths()

    cmd = [
        PYTHON_BIN, "main.py",
        "--listen", "127.0.0.1",
        "--port", str(COMFY_PORT),
        "--highvram",
        "--dont-upcast-attention",
        "--disable-auto-launch",
        "--disable-cuda-malloc"
    ]
    logger.info(f"Starting ComfyUI daemon: {' '.join(cmd)}")
    env = os.environ.copy()
    comfy_process = subprocess.Popen(
        cmd,
        cwd=str(COMFY_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1
    )

    def log_comfy():
        for line in comfy_process.stdout:
            clean = line.strip()
            if clean:
                logger.info(f"[ComfyUI] {clean}")

    threading.Thread(target=log_comfy, daemon=True).start()

def wait_for_comfyui(timeout: int = 45) -> bool:
    """Waits until ComfyUI responds to health check."""
    start_t = time.time()
    url = f"{COMFY_URL}/system_stats"
    while time.time() - start_t < timeout:
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    logger.info("ComfyUI is online and ready!")
                    return True
        except Exception:
            time.sleep(1)
    logger.error("Timed out waiting for ComfyUI to become available.")
    return False

def free_comfyui_memory():
    """Signals ComfyUI to release VRAM before heavy video upscales."""
    try:
        url = f"{COMFY_URL}/free"
        payload = json.dumps({"unload_models": True, "free_memory": True}).encode("utf-8")
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("ComfyUI memory freed successfully.")
    except Exception as e:
        logger.warning(f"Could not free ComfyUI memory: {e}")

def comfy_upload_image(file_path: Path) -> str:
    """Uploads an image to ComfyUI input folder via multipart form."""
    import mimetypes
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    filename = file_path.name
    mime_type, _ = mimetypes.guess_type(str(file_path))
    mime_type = mime_type or "application/octet-stream"

    with open(file_path, "rb") as f:
        file_bytes = f.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + file_bytes + (
        f"\r\n--{boundary}\r\n"
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        f"true\r\n--{boundary}--\r\n"
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res.get("name", filename)

def comfy_queue_prompt(prompt_workflow: Dict[str, Any]) -> str:
    """Submits workflow graph to ComfyUI queue."""
    payload = json.dumps({"prompt": prompt_workflow}).encode("utf-8")
    req = urllib.request.Request(
        f"{COMFY_URL}/prompt",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        prompt_id = res.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI prompt submission failed: {res}")
        return prompt_id

def comfy_wait_for_output(prompt_id: str, timeout: int = 180, on_progress=None) -> List[Path]:
    """Polls history until prompt_id is done, then retrieves output images."""
    start_t = time.time()
    while time.time() - start_t < timeout:
        try:
            req = urllib.request.Request(f"{COMFY_URL}/history/{prompt_id}", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if prompt_id in data:
                    item = data[prompt_id]
                    outputs = item.get("outputs", {})
                    output_files: List[Path] = []
                    for node_id, node_output in outputs.items():
                        if "images" in node_output:
                            for img_info in node_output["images"]:
                                fname = img_info["filename"]
                                subfolder = img_info.get("subfolder", "")
                                img_type = img_info.get("type", "output")
                                # ComfyUI save path
                                base_out = COMFY_DIR / "output"
                                if subfolder:
                                    img_path = base_out / subfolder / fname
                                else:
                                    img_path = base_out / fname
                                if img_path.exists():
                                    output_files.append(img_path)
                                else:
                                    # Fetch via /view endpoint
                                    view_url = f"{COMFY_URL}/view?filename={fname}&subfolder={subfolder}&type={img_type}"
                                    tmp_path = DATA_DIR / "temp" / fname
                                    tmp_path.parent.mkdir(parents=True, exist_ok=True)
                                    urllib.request.urlretrieve(view_url, str(tmp_path))
                                    output_files.append(tmp_path)
                    if output_files:
                        return output_files
        except Exception as e:
            logger.debug(f"History poll error: {e}")
        time.sleep(0.5)

    raise TimeoutError(f"ComfyUI execution timed out after {timeout} seconds.")

# ---------------------------------------------------------------------------
# Workflow Builders: Qwen Image 2.1 Turbo (NVFP4)
# ---------------------------------------------------------------------------
TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0"

def build_qwen_t2i_workflow(prompt: str, width: int = 1024, height: int = 1024, seed: int = 0, negative_prompt: str = "") -> Dict[str, Any]:
    """Builds standard 8-step Text-to-Image workflow for Qwen 2.1 Turbo NVFP4."""
    if seed <= 0:
        seed = int(time.time() * 1000) % (2**31)

    return {
        "1": {
            "inputs": {
                "unet_name": "qwen_image_2.1_turbo_nvfp4.safetensors",
                "weight_dtype": "default"
            },
            "class_type": "UNETLoader"
        },
        "2": {
            "inputs": {
                "clip_name": "qwen3vl_8b_turbo_nvfp4.safetensors",
                "type": "qwen_image",
                "device": "default"
            },
            "class_type": "CLIPLoader"
        },
        "3": {
            "inputs": {
                "vae_name": "qwen_image_2.1_vae_bf16.safetensors"
            },
            "class_type": "VAELoader"
        },
        "4": {
            "inputs": {
                "prompt": prompt,
                "negative_prompt": negative_prompt,
                "resolution": max(width, height),
                "clip": ["2", 0]
            },
            "class_type": "TextEncodeQwenImage21"
        },
        "5": {
            "inputs": {
                "width": width,
                "height": height,
                "batch_size": 1
            },
            "class_type": "EmptyLatentImage"
        },
        "9": {
            "inputs": {
                "noise_seed": seed
            },
            "class_type": "RandomNoise"
        },
        "10": {
            "inputs": {
                "model": ["1", 0],
                "conditioning": ["4", 0]
            },
            "class_type": "BasicGuider"
        },
        "11": {
            "inputs": {
                "sampler_name": "euler"
            },
            "class_type": "KSamplerSelect"
        },
        "12": {
            "inputs": {
                "sigmas": TURBO_SIGMAS
            },
            "class_type": "ManualSigmas"
        },
        "6": {
            "inputs": {
                "noise": ["9", 0],
                "guider": ["10", 0],
                "sampler": ["11", 0],
                "sigmas": ["12", 0],
                "latent_image": ["5", 0]
            },
            "class_type": "SamplerCustomAdvanced"
        },
        "7": {
            "inputs": {
                "samples": ["6", 0],
                "vae": ["3", 0]
            },
            "class_type": "VAEDecode"
        },
        "8": {
            "inputs": {
                "filename_prefix": f"QwenTurbo/Gen_{int(time.time())}",
                "images": ["7", 0]
            },
            "class_type": "SaveImage"
        }
    }

def build_qwen_image_edit_workflow(comfy_image_name: str, edit_prompt: str, seed: int = 0, negative_prompt: str = "") -> Dict[str, Any]:
    """Builds reference-conditioned Image Editing workflow for Qwen 2.1 Turbo NVFP4."""
    if seed <= 0:
        seed = int(time.time() * 1000) % (2**31)

    return {
        "1": {
            "inputs": {
                "unet_name": "qwen_image_2.1_turbo_nvfp4.safetensors",
                "weight_dtype": "default"
            },
            "class_type": "UNETLoader"
        },
        "2": {
            "inputs": {
                "clip_name": "qwen3vl_8b_turbo_nvfp4.safetensors",
                "type": "qwen_image",
                "device": "default"
            },
            "class_type": "CLIPLoader"
        },
        "3": {
            "inputs": {
                "vae_name": "qwen_image_2.1_vae_bf16.safetensors"
            },
            "class_type": "VAELoader"
        },
        "21": {
            "inputs": {
                "image": comfy_image_name,
                "upload": "image"
            },
            "class_type": "LoadImage"
        },
        "4": {
            "inputs": {
                "clip": ["2", 0],
                "images.image_1": ["21", 0],
                "vae": ["3", 0],
                "prompt": edit_prompt,
                "negative_prompt": negative_prompt,
                "resolution": 1024
            },
            "class_type": "TextEncodeQwenImage21"
        },
        "9": {
            "inputs": {
                "noise_seed": seed
            },
            "class_type": "RandomNoise"
        },
        "10": {
            "inputs": {
                "model": ["1", 0],
                "conditioning": ["4", 0]
            },
            "class_type": "BasicGuider"
        },
        "11": {
            "inputs": {
                "sampler_name": "euler"
            },
            "class_type": "KSamplerSelect"
        },
        "12": {
            "inputs": {
                "sigmas": TURBO_SIGMAS
            },
            "class_type": "ManualSigmas"
        },
        "6": {
            "inputs": {
                "noise": ["9", 0],
                "guider": ["10", 0],
                "sampler": ["11", 0],
                "sigmas": ["12", 0],
                "latent_image": ["4", 2]  # LATENT output from TextEncodeQwenImage21
            },
            "class_type": "SamplerCustomAdvanced"
        },
        "7": {
            "inputs": {
                "samples": ["6", 0],
                "vae": ["3", 0]
            },
            "class_type": "VAEDecode"
        },
        "8": {
            "inputs": {
                "filename_prefix": f"QwenTurbo/Edit_{int(time.time())}",
                "images": ["7", 0]
            },
            "class_type": "SaveImage"
        }
    }

# ---------------------------------------------------------------------------
# SeedVR2 Inference Engine
# ---------------------------------------------------------------------------
def run_seedvr2_upscale(input_path: Path, output_dir: Path, resolution: int = 2160, media_type: str = "image",
                        batch_size: int = 1, temporal_overlap: int = 3, color_corr: str = "lab", on_progress=None) -> Path:
    """Runs SeedVR2 upscale for either image or video via CLI inference."""
    cli_path = SEEDVR2_DIR / "inference_cli.py"
    if not cli_path.exists():
        raise RuntimeError(f"SeedVR2 inference_cli.py not found at {cli_path}")

    seedvr2_models = MODELS_DIR / "SEEDVR2"
    cmd = [
        PYTHON_BIN, str(cli_path),
        str(input_path),
        "--output", str(output_dir),
        "--model_dir", str(seedvr2_models),
        "--dit_model", "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
        "--resolution", str(resolution),
        "--color_correction", color_corr
    ]

    if media_type == "video":
        cmd.extend([
            "--batch_size", str(batch_size),
            "--temporal_overlap", str(temporal_overlap),
            "--video_backend", "ffmpeg"
        ])
        if resolution >= 1440:
            cmd.extend(["--vae_encode_tiled", "--vae_decode_tiled"])
    else:
        cmd.extend(["--batch_size", "1"])

    logger.info(f"Running SeedVR2: {' '.join(cmd)}")
    env = os.environ.copy()
    p = subprocess.Popen(
        cmd,
        cwd=str(SEEDVR2_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1
    )

    log_lines = []
    for line in p.stdout:
        clean = line.strip()
        if clean:
            log_lines.append(clean)
            logger.info(f"[SeedVR2] {clean}")
            if on_progress:
                on_progress(clean)

    p.wait()
    if p.returncode != 0:
        tail = "\n".join(log_lines[-20:])
        raise RuntimeError(f"SeedVR2 process exited with code {p.returncode}:\n{tail}")

    # Discover generated file
    candidates = list(output_dir.glob("*.*"))
    if not candidates:
        raise RuntimeError("SeedVR2 completed but produced no output file.")

    # Return most recent non-empty file
    candidates = [c for c in candidates if c.stat().st_size > 0 and c != input_path]
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    if not candidates:
        raise RuntimeError("No valid output file found in output directory.")
    return candidates[0]

# ---------------------------------------------------------------------------
# Background Worker Loop
# ---------------------------------------------------------------------------
def worker_loop():
    while True:
        job_id = job_queue.get()
        if job_id is None:
            break

        with jobs_lock:
            job = jobs.get(job_id)
            if not job:
                job_queue.task_done()
                continue
            job["status"] = "processing"
            job["started_at"] = time.time()
            job["progress"] = 10

        out_job_dir = OUTPUTS_DIR / job_id
        out_job_dir.mkdir(parents=True, exist_ok=True)

        try:
            job_type = job["type"]
            logger.info(f"Processing job {job_id} [type: {job_type}]")

            if job_type == "generate":
                # Text-to-Image via Qwen 2.1 Turbo
                prompt = job["prompt"]
                width = job.get("width", 1024)
                height = job.get("height", 1024)
                seed = job.get("seed", 0)
                neg = job.get("negative_prompt", "")

                with jobs_lock:
                    job["step_info"] = f"Qwen Turbo 8-step generation ({width}x{height})..."
                    job["progress"] = 25

                wf = build_qwen_t2i_workflow(prompt, width, height, seed, neg)
                prompt_id = comfy_queue_prompt(wf)

                with jobs_lock:
                    job["step_info"] = "Executing Euler sampler (CFG 1.0)..."
                    job["progress"] = 60

                out_files = comfy_wait_for_output(prompt_id, timeout=120)
                if not out_files:
                    raise RuntimeError("No output received from ComfyUI.")

                final_name = f"gen_{job_id[:8]}.png"
                dest_path = out_job_dir / final_name
                shutil.copy2(out_files[0], dest_path)

                with jobs_lock:
                    job["result_file"] = final_name
                    job["media_type"] = "image"
                    job["resolution_label"] = f"{width}×{height}"
                    job["progress"] = 100

            elif job_type == "edit":
                # Image Editing via Qwen 2.1 Turbo Reference conditioning
                input_path = Path(job["input_path"])
                edit_prompt = job["prompt"]
                seed = job.get("seed", 0)
                neg = job.get("negative_prompt", "")

                with jobs_lock:
                    job["step_info"] = "Loading image into Qwen3-VL 8B vision encoder..."
                    job["progress"] = 20

                comfy_img = comfy_upload_image(input_path)

                with jobs_lock:
                    job["step_info"] = "Applying instructed edits..."
                    job["progress"] = 50

                wf = build_qwen_image_edit_workflow(comfy_img, edit_prompt, seed, neg)
                prompt_id = comfy_queue_prompt(wf)

                out_files = comfy_wait_for_output(prompt_id, timeout=120)
                if not out_files:
                    raise RuntimeError("No output received from ComfyUI for image edit.")

                final_name = f"edited_{job_id[:8]}.png"
                dest_path = out_job_dir / final_name
                shutil.copy2(out_files[0], dest_path)

                with jobs_lock:
                    job["result_file"] = final_name
                    job["media_type"] = "image"
                    job["progress"] = 100

            elif job_type in ("upscale_photo", "upscale_video"):
                input_path = Path(job["input_path"])
                media_type = "video" if job_type == "upscale_video" else "image"
                res = int(job.get("resolution", 2160))
                batch_size = job.get("batch_size", 17 if media_type == "video" else 1)
                temporal_overlap = job.get("temporal_overlap", 3 if media_type == "video" else 0)

                with jobs_lock:
                    job["step_info"] = f"SeedVR2 Ultra-HD upscaling ({res}p)..."
                    job["progress"] = 30

                free_comfyui_memory()

                def progress_cb(line: str):
                    with jobs_lock:
                        job["step_info"] = line[:100]

                res_path = run_seedvr2_upscale(
                    input_path=input_path,
                    output_dir=out_job_dir,
                    resolution=res,
                    media_type=media_type,
                    batch_size=batch_size,
                    temporal_overlap=temporal_overlap,
                    on_progress=progress_cb
                )

                final_name = res_path.name
                with jobs_lock:
                    job["result_file"] = final_name
                    job["media_type"] = media_type
                    job["resolution_label"] = f"{res}p"
                    job["progress"] = 100

            elif job_type == "combo":
                # Phase 1: Qwen T2I Generation
                prompt = job["prompt"]
                width = job.get("width", 1024)
                height = job.get("height", 1024)
                seed = job.get("seed", 0)

                with jobs_lock:
                    job["step_info"] = "Step 1/2: Generating base composition with Qwen Turbo..."
                    job["progress"] = 20

                wf = build_qwen_t2i_workflow(prompt, width, height, seed)
                prompt_id = comfy_queue_prompt(wf)
                out_files = comfy_wait_for_output(prompt_id, timeout=120)
                if not out_files:
                    raise RuntimeError("Combo Phase 1 failed: No output from Qwen.")

                gen_base_path = out_job_dir / f"base_{job_id[:8]}.png"
                shutil.copy2(out_files[0], gen_base_path)

                # Phase 2: SeedVR2 4K Upscale
                with jobs_lock:
                    job["step_info"] = "Step 2/2: SeedVR2 4K UHD neural upscaling (2160p)..."
                    job["progress"] = 55

                free_comfyui_memory()

                upscaled_path = run_seedvr2_upscale(
                    input_path=gen_base_path,
                    output_dir=out_job_dir,
                    resolution=2160,
                    media_type="image",
                    batch_size=1
                )

                final_name = upscaled_path.name
                with jobs_lock:
                    job["result_file"] = final_name
                    job["base_file"] = gen_base_path.name
                    job["media_type"] = "image"
                    job["resolution_label"] = "4K (2160p)"
                    job["progress"] = 100

            with jobs_lock:
                job["status"] = "completed"
                job["finished_at"] = time.time()
                job["duration"] = round(job["finished_at"] - job["started_at"], 2)
                job["step_info"] = f"Completed in {job['duration']}s"

            logger.info(f"Job {job_id} successfully completed in {job['duration']}s")

        except Exception as e:
            logger.error(f"Job {job_id} failed: {e}", exc_info=True)
            with jobs_lock:
                job["status"] = "failed"
                job["error"] = str(e)
                job["finished_at"] = time.time()
                job["step_info"] = f"Error: {str(e)[:120]}"

        finally:
            job_queue.task_done()

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def get_stats():
    gpu = get_gpu_info()
    with jobs_lock:
        active_count = sum(1 for j in jobs.values() if j["status"] in ("queued", "processing"))
    return {
        "status": "online",
        "gpu": gpu,
        "queue_count": active_count,
        "total_jobs": len(jobs)
    }

@app.post("/api/generate")
async def api_generate(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    try:
        w_raw = int(data.get("width", 1024))
    except (ValueError, TypeError):
        w_raw = 1024
    try:
        h_raw = int(data.get("height", 1024))
    except (ValueError, TypeError):
        h_raw = 1024
    try:
        seed = int(data.get("seed", 0))
    except (ValueError, TypeError):
        seed = 0

    width = normalize_dim(w_raw, 1024)
    height = normalize_dim(h_raw, 1024)
    negative_prompt = str(data.get("negative_prompt", "")).strip()

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "type": "generate",
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "negative_prompt": negative_prompt,
        "status": "queued",
        "created_at": time.time(),
        "progress": 0,
        "step_info": "Queued for generation"
    }

    with jobs_lock:
        jobs[job_id] = job
    job_queue.put(job_id)

    return {"status": "queued", "job_id": job_id}

@app.post("/api/edit")
async def api_edit(
    file: UploadFile = File(...),
    prompt: str = Form(...),
    seed: int = Form(0),
    negative_prompt: str = Form("")
):
    if not prompt.strip():
        raise HTTPException(status_code=400, detail="Edit prompt cannot be empty.")

    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail="File must be an image (PNG, JPG, WebP).")

    job_id = str(uuid.uuid4())
    in_path = UPLOADS_DIR / f"upload_{job_id}{ext}"
    with open(in_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job = {
        "id": job_id,
        "type": "edit",
        "input_path": str(in_path),
        "prompt": prompt.strip(),
        "seed": seed,
        "negative_prompt": negative_prompt.strip(),
        "status": "queued",
        "created_at": time.time(),
        "progress": 0,
        "step_info": "Queued for photo editing"
    }

    with jobs_lock:
        jobs[job_id] = job
    job_queue.put(job_id)

    return {"status": "queued", "job_id": job_id}

@app.post("/api/upscale")
async def api_upscale(
    file: UploadFile = File(...),
    media_type: str = Form("image"),
    resolution: int = Form(2160),
    batch_size: int = Form(1),
    temporal_overlap: int = Form(3)
):
    ext = Path(file.filename).suffix.lower()
    if media_type == "video" and ext not in VIDEO_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported video extension: {ext}")
    if media_type == "image" and ext not in IMAGE_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported image extension: {ext}")

    job_id = str(uuid.uuid4())
    in_path = UPLOADS_DIR / f"upload_{job_id}{ext}"
    with open(in_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    job_type = "upscale_video" if media_type == "video" else "upscale_photo"
    job = {
        "id": job_id,
        "type": job_type,
        "input_path": str(in_path),
        "resolution": resolution,
        "batch_size": batch_size,
        "temporal_overlap": temporal_overlap,
        "status": "queued",
        "created_at": time.time(),
        "progress": 0,
        "step_info": f"Queued for {resolution}p upscale"
    }

    with jobs_lock:
        jobs[job_id] = job
    job_queue.put(job_id)

    return {"status": "queued", "job_id": job_id}

@app.post("/api/combo")
async def api_combo(request: Request):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        data = await request.json()
    else:
        form = await request.form()
        data = dict(form)

    prompt = str(data.get("prompt", "")).strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Prompt cannot be empty.")

    try:
        w_raw = int(data.get("width", 1024))
    except (ValueError, TypeError):
        w_raw = 1024
    try:
        h_raw = int(data.get("height", 1024))
    except (ValueError, TypeError):
        h_raw = 1024
    try:
        seed = int(data.get("seed", 0))
    except (ValueError, TypeError):
        seed = 0

    width = normalize_dim(w_raw, 1024)
    height = normalize_dim(h_raw, 1024)

    job_id = str(uuid.uuid4())
    job = {
        "id": job_id,
        "type": "combo",
        "prompt": prompt,
        "width": width,
        "height": height,
        "seed": seed,
        "status": "queued",
        "created_at": time.time(),
        "progress": 0,
        "step_info": "Queued for 1-Click 4K Combo"
    }

    with jobs_lock:
        jobs[job_id] = job
    job_queue.put(job_id)

    return {"status": "queued", "job_id": job_id}

@app.get("/api/status/{job_id}")
def get_job_status(job_id: str):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found.")
        return job

@app.get("/api/jobs")
def list_jobs():
    with jobs_lock:
        sorted_jobs = sorted(jobs.values(), key=lambda j: j.get("created_at", 0), reverse=True)
        return sorted_jobs[:50]

@app.get("/api/gallery")
def list_gallery():
    items = []
    with jobs_lock:
        for job_id, job in jobs.items():
            if job.get("status") == "completed" and job.get("result_file"):
                fname = job["result_file"]
                fpath = OUTPUTS_DIR / job_id / fname
                if fpath.exists():
                    items.append({
                        "job_id": job_id,
                        "filename": fname,
                        "type": job.get("type"),
                        "media_type": job.get("media_type", "image"),
                        "prompt": job.get("prompt", ""),
                        "duration": job.get("duration", 0),
                        "resolution": job.get("resolution_label", ""),
                        "size_mb": round(fpath.stat().st_size / (1024 * 1024), 2),
                        "created_at": job.get("finished_at", job.get("created_at")),
                        "url": f"/api/file/{job_id}/{fname}"
                    })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items

@app.get("/api/file/{job_id}/{filename}")
def get_output_file(job_id: str, filename: str):
    fpath = OUTPUTS_DIR / job_id / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    ext = fpath.suffix.lower()
    media_type = "video/mp4" if ext in VIDEO_EXTS else "image/png"
    return FileResponse(str(fpath), media_type=media_type)

@app.get("/api/download/{job_id}/{filename}")
def download_output_file(job_id: str, filename: str):
    fpath = OUTPUTS_DIR / job_id / filename
    if not fpath.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(str(fpath), filename=filename)

@app.delete("/api/job/{job_id}")
def delete_job(job_id: str):
    with jobs_lock:
        if job_id in jobs:
            del jobs[job_id]
    job_dir = OUTPUTS_DIR / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
    return {"status": "deleted"}

# Serve static files & index
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Qwen Upscaler Studio is running!</h1>"

# ---------------------------------------------------------------------------
# Startup
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="::", port=port, log_level="info")

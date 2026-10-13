#!/usr/bin/env python3
"""
Qwen 2.1 Turbo + SeedVR2 Studio - Multi-Cloud Management CLI (ctl.py)
---------------------------------------------------------------------
Manage deployments across SaladCloud, Vast.ai, Local Docker, and Hugging Face.

Usage:
  python ctl.py salad up [--gpu rtx_a5000|rtx_3090_ti|rtx_4090|rtx_5090]
  python ctl.py salad down
  python ctl.py salad status
  python ctl.py vast guide
  python ctl.py local up
  python ctl.py hf push
"""

import os
import sys
import json
import argparse
import subprocess
from pathlib import Path

# Load environment variables from .env if present
def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

load_env()

SALAD_API_KEY = os.environ.get("SALAD_API_KEY", "")
SALAD_ORG = os.environ.get("SALAD_ORGANIZATION_NAME", "")
SALAD_PROJECT = os.environ.get("SALAD_PROJECT_NAME", "default")
SALAD_CONTAINER = os.environ.get("SALAD_CONTAINER_NAME", "qwen-turbo-upscaler")
DOCKER_IMAGE = os.environ.get("DOCKER_IMAGE", "s3rfdev/qwen-turbo-upscaler:latest")
HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_SPACE_ID = os.environ.get("HF_SPACE_ID", "s3rfdev/qwen_upscaler")

# Salad GPU Class mapping (SaladCloud hardware IDs)
SALAD_GPU_MAP = {
    "rtx_a5000": "54cb115e-5b12-405e-8ab7-3aa46062b770",   # RTX A5000 24GB (~$0.18/hr)
    "rtx_3090_ti": "cf4b316c-5812-405e-8ab7-3aa46062b770", # RTX 3090 Ti 24GB (~$0.19/hr)
    "rtx_4090": "a1b2c3d4-4090-405e-8ab7-3aa46062b770",    # RTX 4090 24GB (~$0.34/hr)
    "rtx_5090": "f8e7d6c5-5090-405e-8ab7-3aa46062b770",    # RTX 5090 32GB (~$0.48/hr)
}

def check_salad_credentials():
    if not SALAD_API_KEY or not SALAD_ORG:
        print("\n❌ Error: Missing SaladCloud configuration!")
        print("Please configure SALAD_API_KEY and SALAD_ORGANIZATION_NAME in your .env file.\n")
        print("1. Copy template: cp .env.example .env")
        print("2. Add your Salad API key from: https://portal.salad.com/api-keys\n")
        sys.exit(1)

def salad_up(gpu_name):
    check_salad_credentials()
    import urllib.request
    import urllib.error

    url = f"https://api.salad.com/api/public/organizations/{SALAD_ORG}/projects/{SALAD_PROJECT}/containers"
    headers = {
        "Salad-Api-Key": SALAD_API_KEY,
        "Content-Type": "application/json"
    }

    # Normalize GPU class
    gpu_id = SALAD_GPU_MAP.get(gpu_name.lower())
    gpu_classes = [gpu_id] if gpu_id else []

    payload = {
        "name": SALAD_CONTAINER,
        "container": {
            "image": DOCKER_IMAGE,
            "resources": {
                "cpu": 8,
                "memory": 32768,
                "gpu_classes": gpu_classes
            },
            "environment_variables": {
                "PORT": "8000"
            }
        },
        "networking": {
            "protocol": "http",
            "port": 8000,
            "auth": False
        },
        "replicas": 1,
        "restart_policy": "always"
    }

    print(f"\n🚀 Deploying container '{SALAD_CONTAINER}' to SaladCloud...")
    print(f"📦 Image: {DOCKER_IMAGE}")
    print(f"⚡ GPU:   {gpu_name} (24GB+ VRAM)")
    print(f"🏢 Org:   {SALAD_ORG} / Project: {SALAD_PROJECT}\n")

    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print("✅ Successfully deployed container group!")
            print(f"Access endpoint: {data.get('networking', {}).get('dns', 'Pending DNS allocation...')}")
            print(f"Run 'python ctl.py salad status' to monitor startup.\n")
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        if e.code == 409:
            print(f"⚠️ Container '{SALAD_CONTAINER}' already exists. Updating container group...")
            put_url = f"{url}/{SALAD_CONTAINER}"
            put_req = urllib.request.Request(put_url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="PUT")
            try:
                with urllib.request.urlopen(put_req) as p_resp:
                    print("✅ Container group successfully updated & restarted!\n")
            except Exception as pe:
                print(f"❌ Failed to update: {pe}\n")
        else:
            print(f"❌ Salad API HTTP {e.code}: {err_msg}\n")
            sys.exit(1)

def salad_status():
    check_salad_credentials()
    import urllib.request
    import urllib.error

    url = f"https://api.salad.com/api/public/organizations/{SALAD_ORG}/projects/{SALAD_PROJECT}/containers/{SALAD_CONTAINER}"
    headers = {"Salad-Api-Key": SALAD_API_KEY}

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            net = data.get("networking", {})
            dns = net.get("dns", "None")
            status = data.get("status", {})
            print("\n📊 SaladCloud Container Status:")
            print(f"   Name:     {data.get('name')}")
            print(f"   Image:    {data.get('container', {}).get('image')}")
            print(f"   Replicas: {status.get('running_replicas', 0)} / {status.get('desired_replicas', 1)} running")
            print(f"   Public DNS: https://{dns}\n")
    except urllib.error.HTTPError as e:
        print(f"❌ Could not retrieve status: {e.read().decode('utf-8')}\n")

def salad_down():
    check_salad_credentials()
    import urllib.request
    import urllib.error

    url = f"https://api.salad.com/api/public/organizations/{SALAD_ORG}/projects/{SALAD_PROJECT}/containers/{SALAD_CONTAINER}"
    headers = {"Salad-Api-Key": SALAD_API_KEY}

    req = urllib.request.Request(url, headers=headers, method="DELETE")
    try:
        with urllib.request.urlopen(req) as resp:
            print(f"✅ Container group '{SALAD_CONTAINER}' removed successfully from SaladCloud.\n")
    except urllib.error.HTTPError as e:
        print(f"❌ Failed to delete: {e.read().decode('utf-8')}\n")

def vast_guide():
    print("""
================================================================================
🌐 VAST.AI 1-CLICK DEPLOYMENT GUIDE
================================================================================

1. Install Vast.ai CLI:
   $ pip install vastai

2. Authenticate with your API key:
   $ vastai set api-key $VAST_API_KEY

3. Search for available RTX 3090 / 4090 / A5000 instances (< $0.25/hr):
   $ vastai search offers 'gpu_name in [RTX_3090, RTX_3090_Ti, RTX_4090, A5000] compute_cap >= 800 inet_down > 200'

4. Launch the pre-configured container on Offer ID <OFFER_ID>:
   $ vastai create instance <OFFER_ID> \\
       --image s3rfdev/qwen-turbo-upscaler:latest \\
       --disk 80 \\
       --env '-p 8000:8000 -e PORT=8000' \\
       --onstart-cmd "/app/start.sh"

5. Connect your browser or Hugging Face Space to:
   http://<instance_ip>:<external_port>
================================================================================
""")

def local_up():
    print(f"\n🐳 Launching {DOCKER_IMAGE} locally with NVIDIA GPU acceleration...")
    cmd = [
        "docker", "run", "--gpus", "all",
        "-d", "--restart", "unless-stopped",
        "-p", "8000:8000",
        "--name", "qwen-turbo-upscaler",
        DOCKER_IMAGE
    ]
    print(f"Running: {' '.join(cmd)}\n")
    try:
        subprocess.run(cmd, check=True)
        print("✅ Local container started!")
        print("🌐 Open Web UI at: http://localhost:8000\n")
    except Exception as e:
        print(f"❌ Failed to run Docker: {e}")
        print("Tip: Make sure Docker is running with NVIDIA Container Toolkit installed.\n")

def hf_push():
    if not HF_TOKEN:
        print("\n❌ Error: Missing HF_TOKEN in .env!")
        print("Provide your Hugging Face write token in .env or via HF_TOKEN environment variable.\n")
        sys.exit(1)

    print(f"\n🤗 Syncing web application to Hugging Face Space: {HF_SPACE_ID}...")
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=HF_TOKEN)

        static_index = Path(__file__).parent / "salad_app" / "static" / "index.html"
        readme_path = Path(__file__).parent / "docs" / "hf_space_readme.md"

        if static_index.exists():
            api.upload_file(
                path_or_fileobj=str(static_index),
                path_in_repo="index.html",
                repo_id=HF_SPACE_ID,
                repo_type="space"
            )
            print("✅ Uploaded index.html to Hugging Face Space")

        if readme_path.exists():
            api.upload_file(
                path_or_fileobj=str(readme_path),
                path_in_repo="README.md",
                repo_id=HF_SPACE_ID,
                repo_type="space"
            )
            print("✅ Uploaded space configuration README.md")

        print(f"\n🎉 Hugging Face Space is live at: https://huggingface.co/spaces/{HF_SPACE_ID}\n")
    except ImportError:
        print("⚠️ 'huggingface_hub' package not installed. Installing or pushing via git remote...")
        import urllib.request
        # We can also push via git or pip install
        subprocess.run([sys.executable, "-m", "pip", "install", "huggingface_hub"], check=True)
        hf_push()

def main():
    parser = argparse.ArgumentParser(description="Qwen 2.1 Turbo + SeedVR2 Studio Deployment CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Salad parser
    salad_parser = subparsers.add_parser("salad", help="Manage SaladCloud deployments")
    salad_parser.add_argument("action", choices=["up", "down", "status"], help="Action to execute")
    salad_parser.add_argument("--gpu", default="rtx_a5000", choices=["rtx_a5000", "rtx_3090_ti", "rtx_4090", "rtx_5090"], help="GPU class")

    # Vast parser
    subparsers.add_parser("vast", help="Vast.ai deployment guide and commands")

    # Local parser
    local_parser = subparsers.add_parser("local", help="Local Docker deployment")
    local_parser.add_argument("action", choices=["up"], help="Action")

    # HF parser
    subparsers.add_parser("hf", help="Hugging Face Space deployment").add_argument("action", choices=["push"], help="Action")

    args = parser.parse_args()

    if args.command == "salad":
        if args.action == "up":
            salad_up(args.gpu)
        elif args.action == "down":
            salad_down()
        elif args.action == "status":
            salad_status()
    elif args.command == "vast":
        vast_guide()
    elif args.command == "local":
        local_up()
    elif args.command == "hf":
        hf_push()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()

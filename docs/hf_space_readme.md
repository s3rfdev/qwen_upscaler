---
title: Qwen 2.1 Turbo & SeedVR2 4K Studio
emoji: ⚡
colorFrom: indigo
colorTo: purple
sdk: static
pinned: false
license: apache-2.0
short_description: Qwen 2.1 Turbo T2I & SeedVR2 4K Super-Resolution
---

# ⚡ Qwen 2.1 Turbo + SeedVR2 4K Studio

Welcome to the **Qwen Upscaler Studio** Hugging Face Space!

This interface is a high-performance web studio connecting to modern open-source vision and super-resolution models:
* **Qwen 2.1 Turbo (NVFP4)**: 8-step lightning text-to-image synthesis in just seconds on any modern NVIDIA GPU.
* **Qwen3-VL 8B**: Precision instruction-guided photo editing preserving subject identity, composition, and physical lighting.
* **SeedVR2 (EMA 3B FP8)**: Next-generation neural super-resolution for upscaling photos and videos to **4K UHD (2160p 60fps)**.
* **1-Click 4K Combo Pipeline**: Composition generated instantly with Qwen Turbo and upscaled into cinema 4K with SeedVR2 in a single automated step.

---

## 🎮 Interactive Demo Mode vs Live GPU Mode

Because generating 4K images and videos requires **24GB+ VRAM**, this public Space runs as a decoupled frontend:

1. **Try Demo Mode:** Click **Try Demo Mode** in the top banner to test the UI, inspect sample 4K renders, and explore the studio features without a backend.
2. **Live Generation with Your GPU:**
   - Deploy your dedicated backend container on **SaladCloud** (~$0.18/hr for RTX A5000 / 3090 Ti):
     ```bash
     python ctl.py salad up --gpu rtx_a5000
     ```
   - Or run locally:
     ```bash
     docker run --gpus all -p 8000:8000 s3rfdev/qwen-turbo-upscaler:latest
     ```
   - Click the **Endpoint** button in the header and paste your URL (e.g. `https://your-node.salad.net` or `http://localhost:8000`).

---

## 🔗 Links & Resources

* **GitHub Repository**: [https://github.com/s3rfdev/qwen_upscaler](https://github.com/s3rfdev/qwen_upscaler)
* **Docker Hub Image**: [s3rfdev/qwen-turbo-upscaler:latest](https://hub.docker.com/r/s3rfdev/qwen-turbo-upscaler)
* **License**: Apache 2.0

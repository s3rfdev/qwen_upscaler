---
title: Qwen 2.1 Turbo & SeedVR2 4K Studio
emoji: ⚡
colorFrom: indigo
colorTo: purple
sdk: static
pinned: false
license: apache-2.0
short_description: Lightning ~1.7s Qwen Turbo T2I & SeedVR2 4K Super-Resolution
---

# ⚡ Qwen 2.1 Turbo + SeedVR2 4K Studio

Welcome to the **Qwen Upscaler Studio** Hugging Face Space!

This interface is a high-performance web studio connecting to modern open-source vision and super-resolution models:
* **Qwen 2.1 Turbo (NVFP4)**: 8-step lightning text-to-image synthesis (~1.7s inference on RTX 5090 / ~3.2s on RTX A5000).
* **Qwen3-VL 8B**: Precision instruction-guided photo editing preserving subject identity, composition, and physical lighting.
* **SeedVR2 (EMA 3B FP8)**: Next-generation neural super-resolution for upscaling photos and videos to **4K UHD (2160p 60fps)**.
* **1-Click 4K Combo Pipeline**: Composition generated instantly with Qwen Turbo and upscaled into cinema 4K with SeedVR2 in a single automated step.

---

## 🚀 How to Connect to GPU Backend

Because generating 4K images and videos requires 24GB+ VRAM, this web client connects dynamically to your own dedicated GPU backend:

1. Deploy the pre-built Docker container on your GPU of choice:
   * **SaladCloud** (~$0.18/hr for RTX A5000 / RTX 3090 Ti):
     ```bash
     python ctl.py salad up --gpu rtx_a5000
     ```
   * **Local Docker (NVIDIA GPU)**:
     ```bash
     docker run --gpus all -p 8000:8000 s3rfdev/qwen-turbo-upscaler:latest
     ```
   * **Vast.ai**: See [GitHub Deployment Guide](https://github.com/s3rfdev/qwen_upscaler#vastai-deployment).

2. Click the **Endpoint / Settings** button (top right of this interface) and enter your backend URL (e.g. `https://your-salad-node.salad.cloud` or `http://localhost:8000`).

3. Start creating instantly!

---

## 🔗 Links & Resources

* **GitHub Repository**: [https://github.com/s3rfdev/qwen_upscaler](https://github.com/s3rfdev/qwen_upscaler)
* **Docker Hub Image**: [s3rfdev/qwen-turbo-upscaler:latest](https://hub.docker.com/r/s3rfdev/qwen-turbo-upscaler)
* **License**: Apache 2.0

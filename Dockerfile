# ==============================================================================
# Qwen 2.1 Turbo (NVFP4) + SeedVR2 4K Upscaler Studio
# Production Dockerfile
# ==============================================================================

FROM s3rfdev/qwen-turbo-upscaler:latest

LABEL maintainer="s3rfdev" \
      description="Lightning ~1.7s Qwen 2.1 Turbo T2I, Qwen3-VL 8B Editing & SeedVR2 4K Super-Resolution" \
      version="1.0.0"

# Application directory
WORKDIR /app

# Ensure correct permissions and clean runtime state
RUN mkdir -p /app/salad_app/outputs /app/salad_app/uploads

# Overlay updated application files & workflows
COPY salad_app /app/salad_app
COPY workflows /app/workflows

# Expose web application port (8000) and optional SSH management (22)
EXPOSE 8000 22

# Health check to ensure FastAPI web service is responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/api/stats || exit 1

# Launch startup script (launches ComfyUI background instance, SeedVR2 daemon, and FastAPI server)
CMD ["/app/start.sh"]

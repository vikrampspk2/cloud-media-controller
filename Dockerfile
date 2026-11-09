FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Install core system packages & MKVToolNix + FFmpeg
RUN apt-get update && apt-get install -y --no-install-recommends \
    mkvtoolnix \
    ffmpeg \
    aria2 \
    curl \
    unzip \
    ca-certificates \
    libvulkan-dev \
    vulkan-tools \
    libvulkan1 \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# FIXED: Exact official GitHub release binary (20220424)
RUN curl -LO https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip && \
    unzip -q realesrgan-ncnn-vulkan-20220424-ubuntu.zip && \
    chmod +x realesrgan-ncnn-vulkan && \
    rm -f realesrgan-ncnn-vulkan-20220424-ubuntu.zip

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

# Render dynamic port binding support
EXPOSE 8080

CMD ["python3", "bot.py"]

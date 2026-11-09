FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    mkvtoolnix \
    ffmpeg \
    aria2 \
    wget \
    unzip \
    libvulkan-dev \
    vulkan-tools \
    libvulkan1 \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# Download Real-ESRGAN & Real-CUGAN ncnn-vulkan binaries
WORKDIR /app
RUN wget -q https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-v0.2.5.0-ubuntu.zip && \
    unzip -q realesrgan-ncnn-vulkan-v0.2.5.0-ubuntu.zip && \
    chmod +x realesrgan-ncnn-vulkan && \
    rm realesrgan-ncnn-vulkan-v0.2.5.0-ubuntu.zip

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python3", "bot.py"]

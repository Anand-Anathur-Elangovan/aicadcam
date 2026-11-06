FROM python:3.11-slim

# System deps for aiortc (libopus, libavformat) and Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libopus-dev \
    pulseaudio \
    wget \
    gnupg2 \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Chrome for Playwright fallback
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] \
        http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY app/ ./app/

RUN pip install --no-cache-dir -e . \
    && playwright install chromium --with-deps

# PulseAudio virtual audio for container environments
RUN mkdir -p /run/pulse \
    && echo "load-module module-null-sink sink_name=virtual_speaker" \
        >> /etc/pulse/default.pa \
    && echo "load-module module-virtual-source source_name=virtual_mic" \
        >> /etc/pulse/default.pa

ENV PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PULSE_SERVER=unix:/run/pulse/native
ENV GOOGLE_CLOUD_PROJECT=vz-it-np-voev-dev-voevdo-0
ENV GOOGLE_CLOUD_LOCATION=us-east4
ENV GOOGLE_GENAI_USE_VERTEXAI=True

CMD ["python", "-m", "uvicorn", "app.agent_runtime_app:agent_runtime", "--host", "0.0.0.0", "--port", "8080"]

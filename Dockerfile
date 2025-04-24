FROM python:3.9-slim-bookworm AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    python3-dev \
    libboost-python-dev \
    libboost-system-dev \
    libssl-dev \
    libgeoip-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

FROM python:3.9-slim-bookworm

WORKDIR /app

COPY --from=builder /root/.local /root/.local
ENV PATH="/root/.local/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libboost-system1.74.0 \
    libssl3 \
    libgeoip1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN mkdir -p /downloads && chmod 777 /downloads
VOLUME ["/downloads"]

CMD ["python3", "main.py"]
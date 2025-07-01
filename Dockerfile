FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Installer toutes les dépendances système et python
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    software-properties-common \
    build-essential \
    python3.9 \
    python3.9-dev \
    python3-pip \
    libboost-python-dev \
    libboost-system-dev \
    libssl-dev \
    libgeoip-dev \
    git \
    curl \
    aria2 \
    ffmpeg \
    qbittorrent-nox && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copier requirements et installer
COPY requirements.txt .
RUN python3.9 -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copier le reste du code
COPY . .

# Créer le dossier data/downloads avec tous les droits
RUN mkdir -p /app/data/downloads && chmod -R 777 /app/data

VOLUME ["/app/data/downloads"]

CMD ["python3.9", "main.py"]

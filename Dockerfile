FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Garante que o yt-dlp do git master já está instalado via requirements.txt

COPY . .

CMD ["sh", "-c", "python categorizer_bot.py"]

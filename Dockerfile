FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y ffmpeg git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Instala o yt-dlp master (versão mais recente com fixes de TikTok)
RUN pip install -U "yt-dlp[default] @ git+https://github.com/yt-dlp/yt-dlp.git@master"

COPY . .

CMD ["sh", "-c", "python categorizer_bot.py"]

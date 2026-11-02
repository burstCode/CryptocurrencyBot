FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BROADCAST_STATE_PATH=/app/data/broadcast_settings.json

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 bot \
    && mkdir -p /app/data \
    && chown bot:bot /app/data

COPY bot.py config.py broadcast.py check_api.py ./
USER bot
CMD ["python", "bot.py"]

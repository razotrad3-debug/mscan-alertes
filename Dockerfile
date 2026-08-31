# Image du bot d'alertes MSCAN (scan + Telegram, sans interface).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements-bot.txt .
RUN pip install --no-cache-dir -r requirements-bot.txt

COPY config.py bot_server.py ./
COPY mmscanner ./mmscanner
# listes de wallets suivis : embarquees dans l'image
COPY smart_wallets.txt followed_wallets.txt* ./

CMD ["python", "bot_server.py"]

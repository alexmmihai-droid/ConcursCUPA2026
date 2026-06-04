FROM python:3.12-slim

WORKDIR /app

# Instalează dependențele
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiază botul
COPY bot.py .

# Pornește botul (polling)
CMD ["python", "bot.py"]

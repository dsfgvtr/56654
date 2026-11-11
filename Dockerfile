FROM python:3.11-slim

# Устанавливаем git, который нужен для скачивания aiomax2 из GitHub
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Запускаем через uvicorn (веб-сервер), а не просто python main.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

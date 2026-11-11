FROM python:3.11-slim

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
RUN mkdir -p /app/data

EXPOSE 8081
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8081"]

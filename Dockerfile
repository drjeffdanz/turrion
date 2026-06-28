FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
# Render provides $PORT; default to 8088 locally.
CMD ["sh", "-c", "uvicorn turrion.main:app --host 0.0.0.0 --port ${PORT:-8088}"]

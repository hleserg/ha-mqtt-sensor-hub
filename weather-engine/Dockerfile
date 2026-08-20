FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

# Runs unprivileged; it needs nothing but an outbound MQTT connection.
RUN useradd --system --uid 10001 --create-home engine
USER engine

CMD ["python", "-u", "/app/main.py"]

FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    ffmpeg \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN wget https://github.com/gyroflow/mp4-merge/releases/download/v0.1.11/mp4_merge-linux64 -O /usr/local/bin/mp4_merge \
    && chmod +x /usr/local/bin/mp4_merge

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

EXPOSE 5000


# Increased timeout and added more worker options
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "300", "--workers", "2", "--threads", "4", "app:app"]

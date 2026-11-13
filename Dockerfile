FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 CHROMIUM_PATH=/usr/bin/chromium STATE_DIR=/app/data
RUN apt-get update && apt-get install -y --no-install-recommends chromium tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.62.0 && useradd --create-home watcher
WORKDIR /app
COPY sources.py watcher.py test_watch.py ./
RUN mkdir /app/data && chown watcher /app/data
USER watcher
ENTRYPOINT ["python", "watcher.py"]
CMD ["watch"]

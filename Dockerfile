FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

# Install deps first (cache-friendly)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . /app

# Ensure runtime user (pwuser) can write under /app
USER root
RUN mkdir -p /app/runs && chown -R pwuser:pwuser /app && chmod +x /app/start.sh
USER pwuser

# Default env
ENV HEADLESS=true \
    SLOW_MO=0 \
    RUNS_DIR=/app/runs \
    PYTHONPATH=/app

EXPOSE 8000

# Use auto-detecting launcher
CMD ["/app/start.sh"]

FROM python:3.11-slim

WORKDIR /app

# Install deps in a separate layer for cache efficiency.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY listener.py .

EXPOSE 8081

# Run with a single worker — the app is async so one process is enough.
CMD ["uvicorn", "listener:app", \
     "--host", "0.0.0.0", \
     "--port", "8081", \
     "--log-level", "info", \
     "--no-access-log"]

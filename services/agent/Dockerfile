FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agent.py prompt.py remediation.py ./

# ANALYSIS_LOG_PATH writes here — mount a volume in production.
RUN mkdir -p /app/data

EXPOSE 8082

CMD ["uvicorn", "agent:app", \
     "--host", "0.0.0.0", \
     "--port", "8082", \
     "--log-level", "info", \
     "--no-access-log"]

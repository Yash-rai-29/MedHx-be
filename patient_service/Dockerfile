# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY patient_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run stage
FROM python:3.12-slim

WORKDIR /app

# Copy dependencies
COPY --from=builder /usr/local /usr/local
ENV PYTHONPATH=/app

# Copy application files
COPY common_code/ /app/common_code/
COPY patient_service/ /app/patient_service/

# Create appuser with non-root privileges
RUN useradd -u 1001 appuser && chown -R appuser:appuser /app
USER 1001

EXPOSE 8001

CMD ["uvicorn", "patient_service.app:app", "--host", "0.0.0.0", "--port", "8001"]

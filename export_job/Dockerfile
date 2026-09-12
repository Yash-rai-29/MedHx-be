# Build stage
FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY export_job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Run stage
FROM python:3.12-slim

WORKDIR /app

# Copy dependencies
COPY --from=builder /usr/local /usr/local
ENV PYTHONPATH=/app

# Copy application files
COPY common_code/ /app/common_code/
COPY export_job/ /app/export_job/

# Create appuser with non-root privileges
RUN useradd -u 1001 appuser && chown -R appuser:appuser /app
USER 1001

ENTRYPOINT ["python", "-m", "export_job.main"]

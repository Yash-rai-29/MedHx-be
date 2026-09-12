FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY account_deletion_job/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /usr/local /usr/local
ENV PYTHONPATH=/app

COPY common_code/ /app/common_code/
COPY patient_service/ /app/patient_service/
COPY account_deletion_job/ /app/account_deletion_job/

RUN useradd -u 1001 appuser && chown -R appuser:appuser /app
USER 1001

ENTRYPOINT ["python", "-m", "account_deletion_job.main"]

# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --target /app/packages -r requirements.txt

# ── Runtime stage ──────────────────────────────────────────────────────────
FROM python:3.12-slim

# Non-root user for security
RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /app/packages /app/packages
ENV PYTHONPATH=/app/packages

# Copy source
COPY main.py .

USER botuser

EXPOSE 8080

CMD ["python", "-m", "uvicorn", "main:api", "--host", "0.0.0.0", "--port", "8080"]

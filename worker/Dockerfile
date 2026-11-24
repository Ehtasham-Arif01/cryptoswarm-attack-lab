FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Run as an unprivileged user (Trivy AVD-DS-0002: containers shouldn't run as root)
RUN useradd --create-home --uid 10001 app
COPY swarm ./swarm
COPY worker ./worker
USER app
CMD ["python", "-m", "worker.worker"]

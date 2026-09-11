FROM python:3.12-slim

# iputils-ping provides the `ping` binary the ICMP latency source shells
# out to (collector/ping/icmp_source.py) - not needed in SIMULATION
# latency mode, but included so ICMP mode works out of the box in Docker.
RUN apt-get update \
    && apt-get install -y --no-install-recommends iputils-ping \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ common/
COPY collector/ collector/
COPY config/ config/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "collector.main"]

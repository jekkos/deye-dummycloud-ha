FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        timeout \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY scripts /app
COPY rootfs/etc/entrypoint.sh /etc/entrypoint.sh
RUN chmod +x /etc/entrypoint.sh
WORKDIR /app

EXPOSE 10000

ENTRYPOINT ["/etc/entrypoint.sh"]

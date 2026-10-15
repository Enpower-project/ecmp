FROM python:3.14-slim
# FROM python:3.10-slim

# Install and refresh system CA certificates
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    update-ca-certificates && \
    apt-get install -y build-essential gcc g++ cmake pkg-config git && \
    rm -rf /var/lib/apt/lists/*

COPY jobs/mqtt_logger/certs/* /usr/local/share/ca-certificates/mqtt/

# Merge them into the system trust store
RUN update-ca-certificates

# Point Python and requests to the Debian CA bundle
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--log-level", "debug", "--access-log"]

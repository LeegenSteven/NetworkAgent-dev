
FROM python:3.13.2-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for numpy/scikit-learn build)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set default env vars (can be overridden at runtime)
ENV SPANNER_INSTANCE=networktopology-instance
ENV SPANNER_DATABASE=networktopology-db
ENV GCS_BUCKET_NAME=network-model-artifacts

CMD ["python", "train.py"]

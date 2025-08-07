FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y build-essential

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY . .

# Use PORT environment variable with default fallback
ENV PORT=${PORT:-5000}

# Start command
CMD gunicorn app:flask_app --workers=1 --threads=4 --worker-class=gevent --timeout 120 --bind 0.0.0.0:$PORT

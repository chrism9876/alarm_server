# Use official Python Alpine base image
FROM python:3.12-alpine

# Environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apk add --no-cache \
    bash \
    curl \
    gcc \
    musl-dev \
    libffi-dev

# Set working directory
WORKDIR /app

# Copy all files from current directory
COPY . /app/

# Install Python dependencies if requirements.txt exists
RUN if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt; \
    fi

# Expose port 8080 for the web server
EXPOSE 8080

# Add Docker health check — tries to connect to port 8080
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl --fail http://localhost:8080/ || exit 1

# Default command to run the Python web server
CMD ["python", "server.py"]
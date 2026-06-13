# ----------------------------------------------------
# 1. BASE IMAGE
# ----------------------------------------------------
# Using the official lightweight Python slim image
FROM python:3.11-slim-bullseye 

# Prevent Python from writing .pyc files to disk
ENV PYTHONDONTWRITEBYTECODE=1
# Unbuffers python output so your logs stream instantly to the Railway console
ENV PYTHONUNBUFFERED=1

# ----------------------------------------------------
# 2. WORKDIR
# ----------------------------------------------------
WORKDIR /app

# ----------------------------------------------------
# 3. MINIMAL SYSTEM DEPENDENCIES (No Playwright Bloat)
# ----------------------------------------------------
# We only retain curl for the health checks and build essentials 
# if any compiled Python wheels need to be built.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        build-essential \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ----------------------------------------------------
# 4. INSTALL PYTHON DEPENDENCIES
# ----------------------------------------------------
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ----------------------------------------------------
# 5. COPY APP
# ----------------------------------------------------
# Copy the source code tree into the working directory container path
COPY . /app/

# ----------------------------------------------------
# 6. START COMMAND
# ----------------------------------------------------
CMD ["python", "main.py"]

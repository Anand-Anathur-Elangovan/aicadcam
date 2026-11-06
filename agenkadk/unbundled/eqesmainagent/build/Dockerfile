# Use the official Python image.
# https://hub.docker.com/_/python
FROM python:3.11-slim

# Set the working directory in the container.
WORKDIR /app

# Copy the requirements file into the container.
COPY requirements.txt .

# Install the dependencies.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code into the container.
COPY . .

# Specify the command to run on container startup.
# The entrypoint is defined in the agent_runtime_config.json
# CMD ["gunicorn", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "app.agent_runtime_app:app"]

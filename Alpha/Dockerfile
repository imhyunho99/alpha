# Stage 1: Base image
FROM python:3.14-slim

# Set the working directory in the container
WORKDIR /app

# Copy the server's requirements file and install dependencies
COPY alpha_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the server's application code
COPY alpha_server/main.py .

# Expose the port the app runs on
EXPOSE 8000

# Run the Uvicorn server
# The command is to run the 'app' instance from the 'main' module
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]

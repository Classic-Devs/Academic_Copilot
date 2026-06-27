# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set work directory
WORKDIR /app

# Install dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files into the container
COPY . /app/

# Copy the entrypoint script from your project root and make it executable
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set the entrypoint script to run on startup
ENTRYPOINT ["/entrypoint.sh"]

# Run the application (keeping your specific port binding)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "academic_copilot.wsgi:application"]
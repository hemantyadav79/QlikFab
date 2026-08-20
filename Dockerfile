# Use Python as the base
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy all the files into the container
COPY . .

# Expose the port (Assuming it runs on 5000 or 5173, you might need to adjust this)
EXPOSE 5000

# Start the application
CMD ["python", "app.py"]

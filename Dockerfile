# Use Ubuntu as base image
FROM ubuntu:latest

# Set the working directory
WORKDIR /app

# Install Python 3 and pip
# Print out Python and pip versions
RUN echo "[ ] Updating package lists..." && \
    apt-get update && \
    echo "[ ] Installing Python 3 and pip..." && \
    apt-get install -y python3 python3-pip && \
    echo "[ ] Cleaning up package cache..." && \
    apt-get clean && \
    echo "[ ] Creating a symbolic link for Python 3..." && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    echo "[ ] Verifying Python and pip versions..." && \
    python --version && \
    pip --version

# Copy requirements.txt to the root directory of the image
COPY requirements.txt /requirements.txt

# Install Python dependencies from requirements.txt
RUN python3 -m pip install -r /requirements.txt

# Create a symbolic link from /app/plex_dupefinder.py to /plex_dupefinder
RUN ln -s /app/plex_dupefinder.py /plex_dupefinder

# Define a volume for the Python application
VOLUME /app

# Define default command
ENTRYPOINT ["/plex_dupefinder"]

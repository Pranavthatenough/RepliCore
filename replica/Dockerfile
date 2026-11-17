# Small Python image with our two files.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY raft.py server.py ./

# NODE_ID, PEERS and PORT come from docker-compose.yml
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-5000} --log-level warning"]

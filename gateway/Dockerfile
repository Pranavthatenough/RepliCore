# Built from the project root (see docker-compose.yml) so it can also copy the frontend.
FROM node:20-slim

WORKDIR /app
COPY gateway/package*.json ./
RUN npm install --omit=dev

COPY gateway/index.js ./
COPY frontend ./frontend
ENV FRONTEND_DIR=/app/frontend

EXPOSE 3000
CMD ["node", "index.js"]

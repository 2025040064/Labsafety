FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/ /app/backend/
RUN pip install --no-cache-dir -r backend/requirements.txt
ENV PYTHONPATH=/app/backend/engine/src:/app/backend PYTHONUNBUFFERED=1 LAB_ROOT=/data LAB_MODE=cloud PORT=80 MPLBACKEND=Agg
EXPOSE 80
CMD ["python", "-m", "server"]

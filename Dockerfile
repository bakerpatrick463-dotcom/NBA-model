FROM python:3.12-slim
WORKDIR /app
COPY nba_bot ./nba_bot
RUN mkdir -p /app/var && chown -R 10001:10001 /app
USER 10001:10001
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
CMD ["python3", "-m", "nba_bot", "worker"]

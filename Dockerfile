# Optional live-ask image. The process exits unless LIVE_ASK=1 at runtime.
# The flight database is not in the image. Mount it and set FLIGHT_DB_PATH.
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir "duckdb>=1.1.0,<2"
COPY src ./src

ENV PYTHONPATH=/app/src
ENV LIVE_ASK=0
ENV FLIGHT_DB_PATH=/data/flights.duckdb
EXPOSE 8080

CMD ["python3", "-m", "flight_analyst", "live", "--host", "0.0.0.0", "--port", "8080"]

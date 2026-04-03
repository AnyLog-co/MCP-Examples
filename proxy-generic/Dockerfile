FROM python:3.11-alpine

WORKDIR /app/
COPY anylog_proxy.py .

RUN apk add --no-cache bash && \
    pip install --upgrade pip setuptools wheel && \
    pip install flask flask-cors requests

ENTRYPOINT ["/bin/sh", "-c", \
  "python3 /app/anylog_proxy.py \
   --anylog-url ${ANYLOG_NODE_URL} \
   --mode ${MODE} \
   --port ${PROXY_PORT} \
   --html-dir /app/html"]
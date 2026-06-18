FROM python:3.14-alpine

WORKDIR /app

COPY requirements.txt .
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libffi-dev \
    && pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

COPY . .

RUN adduser -D appuser \
    && mkdir -p uploads outputs config \
    && chown appuser:appuser uploads outputs config

USER appuser

EXPOSE 5002

CMD ["gunicorn", "--bind", "0.0.0.0:5002", "--workers", "1", "--threads", "8", "app:app"]

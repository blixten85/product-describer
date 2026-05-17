FROM python:3.14-alpine

RUN apk update && apk upgrade && apk add --no-cache xz-libs && rm -rf /var/cache/apk/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY main.py app.py ./
COPY templates/ templates/

RUN mkdir -p uploads outputs

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "4", "--timeout", "300", "app:app"]

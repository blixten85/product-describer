FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py app.py ./
COPY templates/ templates/

RUN mkdir -p uploads outputs

EXPOSE 5000

# Web UI by default; override with: docker run ... python main.py run /data/file.csv
CMD ["python", "app.py"]

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app app
COPY media media
# the SQLite database lives in /srv/data — mount a volume there
CMD ["python", "-m", "app.main"]

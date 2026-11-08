FROM python:3.14-alpine

RUN apk add --no-cache tzdata

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir --no-compile -r requirements.txt \
 && pip uninstall -y pip \
 && rm -f /usr/local/bin/pip /usr/local/bin/pip3 /usr/local/bin/pip3.14

COPY . .

WORKDIR /data

ENTRYPOINT ["python", "-m", "ytscrobbler"]

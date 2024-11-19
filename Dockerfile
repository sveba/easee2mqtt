FROM python:3-slim
LABEL org.opencontainers.image.source = "https://github.com/sveba/easee2mqtt"

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .

CMD ["python", "easee2mqtt.py"]
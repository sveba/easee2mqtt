FROM python:3-slim
LABEL org.opencontainers.image.source = "https://github.com/sveba/easee2mqtt"

WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install --upgrade pip && \
    pip3 install -r requirements.txt

COPY ./easee2mqtt.py /app/easee2mqtt.py

CMD ["python", "/app/easee2mqtt.py"]
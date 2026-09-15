FROM python:3-slim
LABEL org.opencontainers.image.source = "https://github.com/sveba/easee2mqtt"

WORKDIR /app

COPY requirements.txt requirements.txt
RUN python -c "from pathlib import Path; data = Path('requirements.txt').read_bytes(); enc = 'utf-16' if data[:2] in (bytes.fromhex('fffe'), bytes.fromhex('feff')) else 'utf-8-sig'; Path('requirements-ci.txt').write_text(data.decode(enc))" \
    && pip3 install -r requirements-ci.txt

COPY . .

CMD ["python", "easee2mqtt.py"]

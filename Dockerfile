FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHESTNUT_ENV=cloud \
    CHESTNUT_HOST=0.0.0.0 \
    PORT=80

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libcap2-bin \
    && setcap 'cap_net_bind_service=+ep' /usr/local/bin/python3.12 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN python version_info.py --write-build-info

RUN useradd --create-home --uid 10001 chestnut && chown -R chestnut:chestnut /app
USER chestnut

# Fail the image build if its runtime user cannot bind the platform probe port.
RUN python -c "import os,socket; assert os.getuid() == 10001; s=socket.socket(); s.bind(('0.0.0.0',80)); s.close(); print('Non-root port 80 bind check passed')"

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '80') + '/health', timeout=12)" || exit 1

CMD ["python", "server.py"]

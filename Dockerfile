FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHESTNUT_ENV=cloud \
    CHESTNUT_HOST=0.0.0.0 \
    PORT=8080

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . ./

RUN python version_info.py --write-build-info

RUN useradd --create-home --uid 10001 chestnut && chown -R chestnut:chestnut /app
USER chestnut

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=15s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8080') + '/health', timeout=12)" || exit 1

CMD ["python", "server.py"]

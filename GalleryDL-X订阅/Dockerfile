FROM python:3.12-slim

RUN pip install --no-cache-dir gallery-dl==1.32.17 \
    && groupadd --gid 1000 archive \
    && useradd --uid 1000 --gid archive --no-create-home archive \
    && mkdir /config /downloads /cache \
    && chown archive:archive /config /downloads /cache

WORKDIR /app
COPY --chown=archive:archive scheduler.py service.py config.example.json ./
COPY --chown=archive:archive shared ./shared
COPY --chown=archive:archive Reader/backend.py Reader/api_v1.py Reader/index.html Reader/connect.html ./Reader/
COPY --chown=archive:archive integration ./integration
ENV TZ=Asia/Taipei PYTHONUNBUFFERED=1 GDL_CONFIG=/config DATA_DIR=/downloads CACHE_DIR=/cache PORT=9090
USER archive
VOLUME ["/config", "/downloads", "/cache"]
EXPOSE 9090
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9090/login', timeout=3)"

CMD ["python", "service.py"]

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 XARCHIVE_DATA=/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 archive \
    && useradd --uid 10001 --gid archive --no-create-home archive \
    && mkdir /data && chown archive:archive /data
COPY --chown=archive:archive xarchive ./xarchive
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8787/health',timeout=3)); assert r['status']=='ok' and r['worker']"
CMD ["python", "-m", "uvicorn", "xarchive.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787", "--workers", "1", "--no-access-log", "--timeout-graceful-shutdown", "25"]

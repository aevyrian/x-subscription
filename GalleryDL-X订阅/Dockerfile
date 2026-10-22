FROM python:3.12-slim

RUN pip install --no-cache-dir \
    -i https://mirrors.aliyun.com/pypi/simple/ \
    gallery-dl

ENV TZ=Asia/Shanghai

CMD ["/config/entrypoint.sh"]
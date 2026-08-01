FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# non-root 실행 — 앱 코드는 읽기 전용으로 두고, 쓰기가 필요한 곳은 엔트리포인트가 넘긴다
RUN useradd --system --uid 10001 --create-home appuser \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chown -R root:root /srv && chmod -R a+rX /srv
ENV PLW_QUOTA_DB=/tmp/quota.sqlite3
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]

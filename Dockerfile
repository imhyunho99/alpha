FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 시스템 의존성 (lxml, scipy 등 빌드용)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.docker.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

# 코드 복사 (alpha_server 패키지 전체 + 클라이언트 파이썬 모듈)
COPY alpha_server ./alpha_server
COPY alpha ./alpha

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "alpha_server.main:app", "--host", "0.0.0.0", "--port", "8000"]

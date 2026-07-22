FROM python:3.12-slim

WORKDIR /app

# OpenCV, curl(헬스체크) 의존성
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Surya 환경변수 기본값
ENV TORCH_DEVICE=cpu \
    DETECTOR_BATCH_SIZE=8 \
    RECOGNITION_BATCH_SIZE=4 \
    OMP_NUM_THREADS=8 \
    MKL_NUM_THREADS=8 \
    PYTHONUNBUFFERED=1

# 기본 CMD는 web 컨테이너 (uvicorn)
# worker 컨테이너는 docker-compose에서 command로 override:
#   command: ["python", "-m", "app.worker"]
# FastAPI app(root_path=settings.ROOT_PATH)에서 처리하므로 --root-path 불필요
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--forwarded-allow-ips", "*"]

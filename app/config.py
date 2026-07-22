"""pydantic-settings 기반 환경변수 설정"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 인프라 (웹서비스 전용 — CLI 로컬 실행은 불필요하므로 기본값 허용.
    # 웹 배포에서 누락 시 DB/Redis 연결 시점에 명확한 에러로 드러남)
    DATABASE_URL: str = ""
    REDIS_URL: str = ""

    # nginx 라우팅 prefix (FastAPI root_path)
    ROOT_PATH: str = "/ebook"

    # 파일 경로
    UPLOAD_DIR: str = "/data/uploads"
    OUTPUT_DIR: str = "/data/outputs"
    TEMP_DIR: str = "/data/temp"
    LOG_DIR: str = "/data/logs"

    # 한계값
    MAX_UPLOAD_BYTES: int = 209715200  # 200MB
    MAX_PAGES: int = 500

    # 디바이스
    DEVICE: str = "cpu"
    ORT_PROVIDERS: str = "CPUExecutionProvider"
    OMP_NUM_THREADS: int = 8
    MKL_NUM_THREADS: int = 8

    # 모델 캐시
    HF_HOME: str = "/models"

    # RQ
    RQ_QUEUE: str = "ebook-converter"
    JOB_TIMEOUT_SECONDS: int = 300  # 5분. 텍스트 PDF만 처리하므로 충분 (V1)

    # V2: 외부 OCR API (BYOK)
    # 주의: 웹 배포 .env에 이 키를 넣으면 웹 업로드 이미지 PDF도 외부 API로 전송되며,
    # JOB_TIMEOUT_SECONDS 상향이 필요해진다
    MISTRAL_API_KEY: str = ""
    OCR_MODEL: str = "mistral-ocr-latest"

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()

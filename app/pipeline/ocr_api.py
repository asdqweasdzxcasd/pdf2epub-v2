"""Mistral OCR API 클라이언트 (BYOK).

- 문서를 chunk_size 페이지 단위로 잘라 요청 (요청 크기·타임아웃 관리)
- 응답 pages의 index를 원본 PDF 절대 페이지 번호로 재작성해 이어붙임
- 429/5xx는 백오프 재시도 (무료 티어 분당 2요청 대응), 4xx는 즉시 실패
"""

import base64
import logging
import time
from pathlib import Path

import fitz
import requests

logger = logging.getLogger(__name__)

OCR_ENDPOINT = "https://api.mistral.ai/v1/ocr"
_RETRY_BACKOFF_SECONDS = 30


class OcrApiError(Exception):
    pass


class MistralOcrClient:
    def __init__(
        self,
        api_key: str,
        model: str = "mistral-ocr-latest",
        chunk_size: int = 40,
        max_retries: int = 3,
    ):
        if not api_key:
            raise OcrApiError("MISTRAL_API_KEY가 비어 있습니다")
        self._api_key = api_key
        self._model = model
        self._chunk_size = chunk_size
        self._max_retries = max_retries

    def process_pdf(self, pdf_path: Path, progress=None) -> list[dict]:
        doc = fitz.open(pdf_path)
        try:
            total = doc.page_count
            chunks = [
                (start, min(start + self._chunk_size, total))
                for start in range(0, total, self._chunk_size)
            ]
            all_pages: list[dict] = []
            for ci, (start, end) in enumerate(chunks):
                sub = fitz.open()
                try:
                    sub.insert_pdf(doc, from_page=start, to_page=end - 1)
                    pdf_bytes = sub.tobytes()
                finally:
                    sub.close()

                size_mb = len(pdf_bytes) / 1_048_576
                logger.info("청크 %d/%d: %d~%d페이지, %.1fMB", ci + 1, len(chunks), start + 1, end, size_mb)
                if size_mb > 45:
                    raise OcrApiError(
                        f"청크 크기 {size_mb:.0f}MB가 API 한도에 근접 — "
                        f"chunk_size를 줄여서 재시도하세요 (현재 {self._chunk_size})"
                    )

                result = self._call(pdf_bytes)
                for page in result.get("pages", []):
                    # 청크 내 상대 index → 원본 절대 페이지 번호
                    page["index"] = start + page.get("index", 0)
                    all_pages.append(page)

                if progress:
                    pct = 20 + int((ci + 1) / len(chunks) * 60)
                    progress.update(pct, "ocr_api", f"OCR {end}/{total} 페이지")
            return all_pages
        finally:
            doc.close()

    def _call(self, pdf_bytes: bytes) -> dict:
        b64 = base64.b64encode(pdf_bytes).decode()
        payload = {
            "model": self._model,
            "document": {
                "type": "document_url",
                "document_url": f"data:application/pdf;base64,{b64}",
            },
            "include_image_base64": False,  # 크롭은 로컬 렌더에서 하므로 불필요
            "include_blocks": True,
            "table_format": "markdown",
        }
        last_status = None
        for attempt in range(self._max_retries):
            try:
                resp = requests.post(
                    OCR_ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=600,
                )
            except requests.RequestException as e:
                # 네트워크 오류(연결 실패·타임아웃)도 무료 티어 불안정성의 일부 — 재시도
                last_status = type(e).__name__
                logger.warning(
                    "OCR API 네트워크 오류 (시도 %d/%d): %s",
                    attempt + 1, self._max_retries, type(e).__name__,
                )
                if attempt + 1 < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            if resp.status_code == 200:
                try:
                    result = resp.json()
                except ValueError:
                    result = None
                if isinstance(result, dict):
                    return result
                # 200인데 본문이 비정상 — 일시 장애일 수 있으니 재시도 대상
                last_status = "malformed-body"
                logger.warning(
                    "OCR API 200이지만 본문 파싱 실패 (시도 %d/%d)",
                    attempt + 1, self._max_retries,
                )
                if attempt + 1 < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            last_status = resp.status_code
            body = getattr(resp, "text", "")[:500]
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "OCR API %d (시도 %d/%d): %s",
                    resp.status_code, attempt + 1, self._max_retries, body,
                )
                if attempt + 1 < self._max_retries:
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            raise OcrApiError(f"OCR API 오류 {resp.status_code}: {body}")
        raise OcrApiError(f"OCR API 재시도 소진 (마지막 상태 {last_status})")

"""CLI 엔트리포인트: PDF -> EPUB 변환

사용법:
    python -m scripts.convert input.pdf
    python -m scripts.convert input.pdf -o output.epub
    python -m scripts.convert input.pdf --temp-dir ./tmp --device cpu
"""

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.device.runtime import detect
from app.pipeline.progress import CliProgress
from app.pipeline.run import run_pipeline


def main():
    parser = argparse.ArgumentParser(description="PDF를 리플로우 EPUB3로 변환")
    parser.add_argument("input", type=Path, help="입력 PDF 파일 경로")
    parser.add_argument(
        "-o", "--output", type=Path, help="출력 EPUB 경로 (기본: 입력파일명.epub)"
    )
    parser.add_argument("--temp-dir", type=Path, help="임시 파일 디렉토리 (기본: 시스템 임시 디렉토리)")
    parser.add_argument("--device", type=str, default=None, help="디바이스 (cpu/cuda/auto)")
    parser.add_argument("--title", type=str, default=None, help="EPUB 제목 (기본: 파일명)")
    parser.add_argument("--keep-temp", action="store_true", help="변환 후 임시 파일 유지")
    parser.add_argument(
        "--ocr", choices=["auto", "api", "off"], default="auto",
        help="이미지 PDF 처리: auto=키 있으면 API, api=강제, off=페이지 이미지 임베드",
    )
    parser.add_argument(
        "--no-trim", dest="trim", action="store_false",
        help="OCR API 경로에서 페이지 여백 자동 트림을 끄고 원본 페이지를 그대로 전송",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="상세 로그 출력")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger = logging.getLogger("convert")

    if not args.input.exists():
        print(f"파일을 찾을 수 없습니다: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not args.input.suffix.lower() == ".pdf":
        print(f"PDF 파일만 지원합니다: {args.input}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or args.input.with_suffix(".epub")
    title = args.title or args.input.stem

    job_id = uuid4().hex[:8]
    if args.temp_dir:
        temp_dir = args.temp_dir / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_base = tempfile.mkdtemp(prefix="ebook-converter-")
        temp_dir = Path(temp_base)

    runtime = detect()
    device = args.device or runtime.torch_device
    logger.info("디바이스: %s", device)

    progress = CliProgress()

    try:
        run_pipeline(
            args.input, output_path, temp_dir, device, title, progress,
            ocr_mode=args.ocr, trim=args.trim,
        )
        print(f"\n변환 완료: {output_path}", file=sys.stderr)

    except Exception as e:
        logger.error("변환 실패: %s", e, exc_info=True)
        sys.exit(1)

    finally:
        if not args.keep_temp and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.debug("임시 파일 정리 완료: %s", temp_dir)
        elif args.keep_temp:
            logger.info("임시 파일 유지: %s", temp_dir)


if __name__ == "__main__":
    main()

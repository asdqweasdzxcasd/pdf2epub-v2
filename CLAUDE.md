# pdf2epub-v2

PDF → EPUB3 변환기 (공개 저장소). 스캔/이미지 PDF는 Mistral OCR API(BYOK), 텍스트 PDF는 로컬 무료 경로.

## 검증 (완료 판정 기준)

```bash
./scripts/verify   # ruff + pytest — exit 0 이어야 "완료"
```

- 모든 코드 변경은 verify 통과 후에만 완료로 보고
- 새 기능·버그 수정은 테스트 먼저(TDD). 픽스처는 tests/fixtures/ 재사용
- venv 재구성: `uv venv && uv pip install -r requirements-cli.txt -r requirements-dev.txt opencv-python-headless numpy ruff`

## 경계

- **공개 저장소다**: 개인정보·실책 콘텐츠·API 키를 절대 커밋하지 않는다 (실책 테스트 데이터는 ../ebook-converter/testdata/ 에만)
- Mistral OCR API 실호출 테스트는 비용 발생 — 사전 승인 후 실행
- 커밋 메시지는 영어

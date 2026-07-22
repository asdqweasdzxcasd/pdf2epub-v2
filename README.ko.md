# pdf2epub-v2

[English →](README.md)

[![CI](https://github.com/asdqweasdzxcasd/pdf2epub-v2/actions/workflows/ci.yml/badge.svg)](https://github.com/asdqweasdzxcasd/pdf2epub-v2/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

스캔본·이미지 PDF를 깔끔하게 리플로우되는 EPUB3로 변환합니다. 다이어그램은
원래 위치에 다시 잘라 붙이고, 표는 OCR 텍스트 뭉치가 아니라 실제 HTML 표로
렌더링합니다. 텍스트 PDF는 API 호출 없이 완전히 무료로 변환됩니다.
한국어 도서로 레이아웃·OCR 품질을 검증했습니다.

## 무엇을 하나요

이런 페이지가 있다면:

<p align="center">
  <img src="docs/assets/sample-page.png" width="45%" alt="원본 PDF 페이지">
  <img src="docs/assets/sample-page-blocks.png" width="45%" alt="블록 탐지 결과: 텍스트, 다이어그램, 표">
</p>

pdf2epub-v2는 페이지마다 텍스트 블록·다이어그램·표를 탐지한 뒤, 리플로우되는
EPUB 콘텐츠로 재구성합니다. 본문은 진짜 문단이 되고, 다이어그램은 렌더링된
페이지 이미지에서 잘라내어 그림으로 재삽입되며, 표는 OCR된 텍스트 나열이
아니라 실제 `<table>` 마크업이 됩니다.

## 왜 만들었나요

[marker](https://github.com/VikParuchuri/marker)나
[MinerU](https://github.com/opendatalab/MinerU) 같은 도구는 PDF를 Markdown으로
바꾸는 데 뛰어나며, LLM 파이프라인에 문서를 넣기에 좋습니다. pdf2epub-v2는
목표가 다릅니다 — **실제로 전자책 리더에서 펼쳐 읽고 싶은 EPUB3 파일**을
만드는 것입니다. 그러려면 다이어그램은 alt-text가 아니라 다이어그램으로
남아야 하고, 표는 텍스트 줄로 납작해지지 않고 표로 남아야 하며, 챕터 구조도
변환 과정에서 살아남아야 합니다. 또한 영어/라틴 문자 중심 도구에서는 흔히
후순위로 밀리는 한국어 도서 레이아웃·OCR 품질을 직접 튜닝하고 검증했습니다.

## 빠른 시작

아직 PyPI 패키지가 없어 소스에서 직접 실행합니다.

```bash
git clone https://github.com/asdqweasdzxcasd/pdf2epub-v2.git
cd pdf2epub-v2
pip install -r requirements-cli.txt

# 스캔본/이미지 PDF에만 필요합니다 (아래 "비용" 참고).
# https://console.mistral.ai/ 에서 무료로 발급 — 신용카드 불필요.
export MISTRAL_API_KEY=your-key-here

python -m scripts.convert your-book.pdf -o your-book.epub
```

번들된 샘플(저작권 걱정 없는 자체 작성 데모 PDF)로 바로 시험해볼 수 있습니다.

```bash
python -m scripts.convert samples/sample.pdf -o sample.epub --ocr api
```

PDF에 이미 텍스트 레이어가 있다면(스캔본이 아니라면) API 키 없이도 됩니다 —
`--ocr api`를 빼면 완전히 로컬에서, 무료로 변환됩니다.

## 동작 방식

```
PDF → 페이지 렌더링 → Mistral OCR(블록 탐지) → 다이어그램 크롭 +
      HTML 표 생성 → EPUB3 조립
```

1. 각 페이지를 이미지로 렌더링하고, 텍스트 레이어가 있으면 그대로 읽습니다
   (API 호출·비용 없음).
2. 이미지/스캔 페이지는 렌더링된 이미지를 Mistral OCR로 보내 텍스트와
   블록 단위 레이아웃(문단, 제목, 그림, 표)을 받아옵니다.
3. 다이어그램 블록은 원본 페이지 렌더링에서 잘라내 이미지로 삽입하고, 표
   블록은 OCR된 텍스트 그대로 두지 않고 실제 HTML `<table>` 마크업으로
   재구성합니다.
4. 제목(heading)을 기준으로 휴리스틱 목차를 만듭니다.
5. 모든 결과를 표준 리플로우 EPUB3 파일로 조립합니다.

## 데이터 흐름과 프라이버시

- 텍스트 PDF는 기기 밖으로 전혀 나가지 않습니다 — 무료 경로는 네트워크
  호출을 하지 않습니다.
- 이미지/스캔 PDF는 렌더링된 페이지 이미지만 Mistral OCR API로 전송됩니다
  (BYOK — 본인의 `MISTRAL_API_KEY`를 사용). 다른 서비스는 문서를 보지
  않습니다.
- **무료 티어 주의**: 신용카드 없이 쓸 수 있는 Mistral의 "Experiment"
  무료 티어는 제출한 입력을 모델 학습에 사용할 수 있습니다. 민감하거나
  기밀인 문서를 변환한다면 Mistral 유료 티어를 쓰거나, `--ocr off`
  모드(OCR·업로드 없이 페이지 이미지만 임베드)를 사용하세요.

## 비용

- 텍스트 PDF: 무료, API 호출 없음.
- Mistral OCR을 쓰는 이미지/스캔 PDF: 페이지당 약 **$0.004**
  (1000페이지당 $2~4). 300페이지 스캔본 도서 한 권이면 대략 **$1.2**.
- 시작할 때 신용카드는 필요 없습니다 — Mistral 무료 티어로 바로 쓸 수
  있지만 속도 제한이 있습니다(아래 "제약사항" 참고).

## 제약사항

- Mistral 무료 티어는 분당 약 2요청으로 제한됩니다. 큰 책은 40페이지
  단위로 나눠 업로드하고 429 응답 시 자동으로 백오프 재시도하므로, 무료
  티어에서는 두꺼운 스캔본일수록 시간이 더 걸립니다.
- 목차는 진짜 의미 구조가 아니라 제목 레벨 휴리스틱으로 만들어지므로,
  챕터가 과하게 쪼개지거나 합쳐지는 경우가 가끔 있습니다.
- 수식은 텍스트/MathML이 아니라 이미지로 렌더링됩니다.
- 특정 페이지의 OCR 결과가 비어 있거나 재시도 후에도 API 호출이
  실패하면, 그 페이지는 조용히 누락되는 대신 페이지 이미지로 임베드되어
  폴백됩니다 — 콘텐츠가 사라지는 일은 없지만, 간혹 검색이 안 되는
  페이지가 섞일 수 있습니다.

## 로드맵

- 정식 PyPI 패키지 배포 (`pip install pdf2epub`)
- 드래그 앤 드롭으로 변환하는 로컬 웹 UI
- Mistral 외 다른 OCR 제공자 지원

## 라이선스

MIT — [LICENSE](LICENSE) 참고.

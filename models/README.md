# models/ — ONNX 모델

이 폴더의 `*.onnx` 파일은 **용량이 커서 git에 커밋하지 않는다**(`.gitignore`). 최초 사용 시
각 모듈의 `ensure_model()` 이 아래 출처에서 자동 다운로드한다(`urllib`, 원자적 tmp→rename).

## 저장 위치 (`app_dirs.py`)

dev/배포 구분 없이 **항상 OS 사용자 데이터 디렉터리**에 저장한다(일관성 — zip 업데이트마다
새 폴더에 풀려도, dev 폴더를 지워도 재다운로드 없음):

- Windows: `%LOCALAPPDATA%\FilmRawstery\models` (머신 전용 대용량 — Roaming 제외)
- macOS: `~/Library/Application Support/FilmRawstery/models` (Caches 는 OS 가 지울 수 있음)
- Linux: `${XDG_DATA_HOME:-~/.local/share}/FilmRawstery/models` (XDG 규약)

**legacy 마이그레이션**: 예전 위치(구버전 frozen 은 exe 옆 `lib/models`, dev 는 이 폴더)에
받아둔 파일은 첫 사용 시 재다운로드 대신 새 위치로 **복사**된다(`app_dirs.materialize`).
GPU 프로빙 캐시(`ai_denoise_device.json`)도 동일. 복사가 끝난 뒤 이 폴더의 대용량 파일은
지워도 된다(.gitignore 규칙은 legacy 안전망으로 유지).

앱을 삭제해도 사용자 디렉터리의 모델은 남는다 — 완전 제거하려면 위 경로를 함께 삭제.

## 하늘 세그멘테이션 (Sky segmentation)

- **모델**: SegFormer-B2, ADE20K 150클래스 시맨틱 세그멘테이션 (하늘 = 클래스 2)
- **사용 파일**: `models/segformer_b2_ade.onnx` (~105 MB)
- **출처(Hugging Face)**: [`Xenova/segformer-b2-finetuned-ade-512-512`](https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512)
  (transformers.js 용 사전 export ONNX)
- **다운로드 URL**:
  `https://huggingface.co/Xenova/segformer-b2-finetuned-ade-512-512/resolve/main/onnx/model.onnx`
- 코드 상수: `sky_seg.py` 의 `_REPO` / `_MODEL_URL` / `MODEL_PATH`

### 모델 변형 (필요 시 교체)

같은 Xenova 계열에 B0~B5 ONNX가 모두 있다. `sky_seg.py` 의 `_REPO`·`MODEL_PATH` 한 줄만 바꾸면
교체된다(모든 변형이 sky=클래스2·ImageNet 정규화 동일).

| 변형 | repo (`Xenova/...`) | 크기(fp32) | 추론(proxy) | 비고 |
|------|---------------------|-----------|-------------|------|
| B0 | `segformer-b0-finetuned-ade-512-512` | ~14 MB | ~0.3 s | 채광창+구름 등에서 하늘 누락 |
| **B2** | `segformer-b2-finetuned-ade-512-512` | ~105 MB | ~1.2 s | **현재 채택(균형점)** |
| B4 | `segformer-b4-finetuned-ade-512-512` | ~260 MB | 느림 | |
| B5 | `segformer-b5-finetuned-ade-640-640` | ~324 MB | ~3.9 s | B2와 품질 거의 동일 → 비권장 |

각 repo의 `onnx/` 폴더에는 `model.onnx`(fp32) 외에 `model_fp16.onnx`, `model_quantized.onnx` 도
있다(배포 용량 축소 옵션, 품질 약간 저하 가능).

### 라이선스 (⚠️ 상업 배포 시 확인)

SegFormer 가중치는 **NVIDIA 원본 라이선스**(연구용 위주, 상업적 사용 제한)에서 유래한다. 앱을
상업적으로 배포할 경우 모델 라이선스를 반드시 확인하고, 필요하면 상업적으로 자유로운 하늘
세그멘테이션 모델로 교체할 것. (자세한 검출 기술 내용: `docs/sky_masking.md`)

## AI 디노이즈 (AI denoise)

- **모델**: NAFNet-SIDD width32 (conv 전용 UNet, 실카메라 노이즈 SIDD 학습)
- **사용 파일**: `models/nafnet_sidd_width32_512.onnx` (~117 MB, 고정 512×512 입력)
- **원 출처**: [megvii-research/NAFNet](https://github.com/megvii-research/NAFNet) — 공식
  가중치 `NAFNet-SIDD-width32.pth`(repo docs/SIDD.md 의 Google Drive 링크)를 값 무변경
  1:1 ONNX 변환한 것(torch↔ort 최대 오차 ~1e-5 검증). LayerNorm2d 는 custom autograd
  Function 이라 수학적으로 동일한 추론용 모듈로 치환 후 export.
- **다운로드 URL** (코드 상수: `ai_denoise.py` 의 `_MODEL_URL` / `MODEL_PATH`):
  `https://github.com/lim8701/FilmRawstery/releases/download/models-v1/nafnet_sidd_width32_512.onnx`
- **고정 512 인 이유**: 타일 크기 통일 + 고정 크기가 EP 그래프 최적화에 유리(NAFNet 자체는
  conv 전용이라 동적도 가능). 512 타일 + 겹침 램프 블렌딩으로 임의 해상도 처리.
- **실행 장치**: GPU EP 우선(`onnxruntime-directml` 의 DirectML — DX12 GPU 전반, macOS 는
  표준 onnxruntime 의 CoreML) → 없거나 초기화 실패 시 CPU 폴백(느려서 앱이 진행 여부를
  사용자에게 확인). 듀얼 GPU 는 최초 1회 디바이스 프로빙 후 `models/ai_denoise_device.json`
  에 캐시(GPU 구성 변경 시 이 파일 삭제 → 재프로빙).

### ⚠️ SCUNet 을 쓰지 않는 이유 (재조사 방지)

처음엔 SCUNet(Apache-2.0, 순수 합성 학습 — 라이선스 최상)을 채택했으나, swin attention 의
소형 연산 수백 개가 **DirectML 에서 가속 불능**으로 실측 판명(RTX 3050 Ti: DML 4.5~82초/타일
vs CPU 5초; 그래프 분할 아님 — DML 단독 실행 성공에도 느림, fp16 도 17% 개선뿐).
conv 전용 NAFNet 은 동일 GPU 146ms/타일(35×). CUDA EP 는 NVIDIA 전용 + 의존성 1~2GB 라 기각.

### 라이선스

NAFNet 코드·가중치는 **MIT License**(+ BasicSR 부분 Apache-2.0). 학습 데이터 SIDD 도
**MIT** — 공식 페이지(abdokamel.github.io/sidd)에 "The dataset and the associated code
repositories are under the MIT License" 명시(1차 출처 확인). 따라서 ONNX 변환본의
자체 재배포(GitHub Releases 호스팅)에 라이선스 제약이 없다(고지 의무만 — NOTICE.txt).
인공 가우시안 노이즈에는 SCUNet 보다 보수적으로 반응하지만 실카메라 고ISO 노이즈가
본래 학습 도메인.

## 사진 캡션 (Photo caption — Florence-2)

- **모델**: Microsoft Florence-2-base-ft (비전-언어, 영어 캡션 생성)
- **사용 파일** (fp32, 총 ~1.1GB — 최초 캡션 생성 시 `caption.ensure_model()` 자동 다운로드):
  `florence2_vision_encoder.onnx`(367MB) / `florence2_embed_tokens.onnx`(158MB) /
  `florence2_encoder_model.onnx`(173MB) / `florence2_decoder_model.onnx`(388MB)
  + 토크나이저/설정 `florence2_vocab.json`·`florence2_merges.txt`·
  `florence2_preprocessor_config.json`·`florence2_generation_config.json`
- **출처(Hugging Face)**: [`onnx-community/Florence-2-base-ft`](https://huggingface.co/onnx-community/Florence-2-base-ft)
  (transformers.js 용 사전 export ONNX). 코드 상수: `caption.py` 의 `_REPO` / `_FILES`.
- **입력**: RAF 내장 JPEG 프리뷰를 EXIF 회전 반영 후 768×768(비율 무시)로 축소.
  토크나이저는 GPT-2식 byte-level BPE 를 `caption.py` 가 직접 구현(의존성 추가 없음).
- **실행**: CPU EP, greedy, 무캐시 디코더 — 짧은 캡션 기준 장당 ~2.7초(비전 1.3s+생성 1.4s).
  가속 여지: `decoder_model_merged.onnx`(KV-cache) + DirectML EP, int8/q4 변형(용량 ~1/4).
- **라이선스**: **MIT** (모델 카드 명시) — 코드(MIT)와 충돌 없음.
- 캡션은 폴더당 `.filmrawsterycaptions.json` 사이드카에 저장(영어; 앱 UI 에서 수정 가능).

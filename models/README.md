# models/ — ONNX 모델

이 폴더의 `*.onnx` 파일은 **용량이 커서 git에 커밋하지 않는다**(`.gitignore`). 최초 실행 시
`sky_seg.ensure_model()` 가 아래 출처에서 자동 다운로드한다(`urllib`, 원자적 tmp→rename).

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

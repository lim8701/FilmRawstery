# RAW 디모자이크 정책 (결정 기록)

## 현재 정책 (옵션 B, 2026-07 채택)
- **프록시(프리뷰)**: 항상 `LINEAR`(쌍선형). full 디코드 후 max_edge 2560 으로 축소 —
  축소되므로 디모자이크 화질이 체감에 거의 영향 없음, 속도 우선.
- **Export(풀해상도, CPU `pipeline.render_full` + GPU `raw_loader.load_full`)**:
  - **Bayer(2×2 CFA)** = `AHD` — 쌍선형 대비 색 모아레·지퍼링·경계 무름 개선(Canon/Nikon/Sony 등).
  - **X-Trans(6×6)** = `LINEAR` — Fuji 는 프록시와 동일 유지(무변경).
  - **CFA 없음/이형(None, Foveon, 모노 등)** = `LINEAR`(안전 폴백).
- 판별: `raw_loader._export_demosaic(raw)` — `raw.raw_pattern.shape == (2,2)` 이면 Bayer.

## 이렇게 정한 이유
- 디모자이크 화질이 실제로 중요한 곳은 **풀해상도 export**(100% 확인). 프록시는 2560 축소라
  미세 디테일이 어차피 사라져 화질 영향이 작음.
- 그래서 "화질이 필요한 곳(Bayer export)만 AHD, 속도가 중요한 프록시는 LINEAR" 로 균형.
- Fuji(X-Trans, 주 개발 기준)는 프록시·export 모두 LINEAR 라 **완전 무변경**(회귀 없음).

## 알려진 트레이드오프 (수용됨)
- Bayer 는 **프록시(LINEAR 축소) ↔ export(AHD)** 의 텍스처/샤픈/NR **미세 결이 살짝 다름**.
  프록시가 저해상도라 체감은 작지만, 프리뷰=Export 원칙에 대한 부분적 예외.

## 추후 재검토 트리거 (다시 고민할 시점)
- 프리뷰=Export 정밀 정합이 Bayer 에서도 요구될 때 → 옵션 C(프록시도 Bayer AHD, 단 디코드 느려짐).
- 더 나은 알고리즘(DCB/DHT) 이나 X-Trans 전용 고품질(Markesteijn) 검토 시.
- 프록시 half_size 도입 등 디코드 파이프라인 개편 시 함께 재평가.

관련 코드: `raw_loader.py`(`_export_demosaic`, `_decode_native(bayer_ahd)`, `load_full`),
`pipeline.py`(`render_full`). 관련 히스토리: 1차 검토에서 export 를 프록시와 맞추려 LINEAR 로 고정한 커밋.

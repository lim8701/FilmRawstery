# RAW Editor (Fuji X100V) — 프로젝트 가이드

PySide6 + QML + GPU 셰이더 기반 RAW(.RAF) 현상/보정 에디터.

## 목표 (가장 중요)

**물리적으로 정확한 알고리즘을 따르는 것을 우선으로 하면서, 그 위에서 Adobe Lightroom이 내는
느낌/반응(세부 파라미터·시각적 결과)을 따라간다.** 즉 기반 알고리즘은 올바른(물리/색과학적으로
타당한) 방식으로 구현하고, 강도·곡선·체감은 라이트룸과 비교해 튜닝한다.

- 두 목표가 충돌하면: 먼저 **올바른 알고리즘**으로 구현하고, 계수/곡선으로 라이트룸 느낌에 맞춘다.
  단순 흉내(작위적 근사)는 정식 구현 전의 **임시(stopgap)** 로만 둔다.
- **현재 디헤이즈는 임시 톤 모델**이다. 물리 기반 Dark Channel Prior(DCP)를 구현했었으나
  어두운 사진에서 라이트룸 체감(낮추면 흰 베일로 밝아짐)과 안 맞아 임시로 톤 모델로 두었다.
  추후 **올바른 안개 모델(DCP/airlight 추정 개선 등)로 되돌리되 라이트룸 반응에 맞추는** 것이 목표.
- 각 효과의 **계수(강도)** 는 라이트룸과 나란히 비교하며 계속 튜닝하는 값이다(아래 표 참조).
  사용자 피드백("너무 강하다/예민하다")이 오면 해당 계수를 조정한다.
- 슬라이더 범위 `-1..1` ↔ 라이트룸 `-100..+100` 대응. ±1에서 "강하지만 비상식적이지 않게",
  ±0.2에서 "미묘하게".

## 실행 / 환경

- 전용 venv 사용:
  ```
  cd C:\California\TEST36\raw_editor
  .\.venv\Scripts\python.exe main.py
  ```
- venv = Python 3.13. 의존성: `requirements.txt` (PySide6, rawpy, numpy, scipy).
- 시작 시 기본 샘플 자동 로드: `C:\Pic\x100v\131_FUJI\DSCF1039.RAF` (main.py `DEFAULT_RAF`).
  이 파일 as-shot 추정 색온도 ≈ 4350K, 평균밝기가 어두운 편(톤/효과 테스트 시 상대변화가 커 보임).

## 셰이더 컴파일 (필수)

`shaders/*.frag` 를 수정하면 **반드시 .qsb 로 재컴파일**해야 반영된다(앱이 시작 시 자동
재컴파일도 하지만, 수정 후 직접 컴파일 권장):
```
.\.venv\Scripts\pyside6-qsb.exe --glsl 120,150,300es --hlsl 50 --msl 12 -o shaders/adjust.frag.qsb shaders/adjust.frag
.\.venv\Scripts\pyside6-qsb.exe --glsl 120,150,300es --hlsl 50 --msl 12 -o shaders/blur.frag.qsb shaders/blur.frag
```

## 검증 방법

- **QML 로드/경고**: `QT_QPA_PLATFORM=offscreen` 으로 엔진 로드 후 `engine.warnings` 수집 →
  경고 0 확인. (예시는 기존 커밋/대화 참조)
- **수치 검증**: `pipeline.render_full(...)` 로 export 결과 배열을 만들어 평균밝기/채널비 등으로
  효과 방향·강도를 확인(헤드리스 가능).
- **GUI 인터랙션(드래그/더블클릭/실시간 미리보기)은 헤드리스로 검증 불가** → 사용자가 직접 실행해
  확인. 마우스 좌표 기반 QTest는 오프스크린에서 레이아웃이 안 잡혀 신뢰 불가(과거 확인됨).

## 아키텍처

```
RAF ─rawpy(절대 Kelvin WB, auto-bright OFF, half_size)─> 프록시 QImage(max_edge 2560)
   │  (wb.py: Planckian+daylight 앵커로 Kelvin→user_wb, as-shot 추정)
   ▼
QML ShaderEffect 파이프라인 (프록시 해상도 FBO에 렌더 → 화면크기로 스케일 표시)
   순서: 노출 → WB프리뷰게인 → 톤영역(hi/sh/wh/bl)
        → 텍스처/클래리티/디헤이즈 → 필름시뮬 3D LUT → 대비 → 톤커브 → 비네팅
   ▼
화면(프록시·실시간 GPU)  /  Export(pipeline.py: 풀해상도 numpy, 동일 단계 재현)
```

### 핵심 설계 결정

- **처리 해상도 ≠ 표시 해상도**: 파이프라인은 프록시(~2560px) 고정 FBO에서만 렌더하고
  ShaderEffectSource로 화면 크기에 스케일. → GPU 부하가 모니터 해상도와 무관(외장 4K 대응).
- **WB는 하이브리드**: 절대 Kelvin은 디코딩(rawpy user_wb)이 담당(정확). 슬라이더 드래그 중에는
  셰이더가 baked→target 상대 게인으로 실시간 프리뷰, 손 떼면(onPressedChanged !pressed)
  재디코딩 확정 → 게인 (1,1,1) 수렴(이중적용 없음). 드래그 프리뷰는 display-space 근사라
  극단 색온도에서 ~10% 오차, 커밋 시 정확값 스냅.
- **로컬 대비(텍스처/클래리티)**: 멀티패스 분리형 가우시안 블러(`blur.frag`). 텍스처=작은반경
  풀해상도, 클래리티=큰반경 1/4 다운샘플. 블러 체인은 srcImage 에만 의존 → 로드 시 1회 계산
  (슬라이더 조작 시 재계산 안 함). 메인 셰이더가 texBlur(b4)/claBlur(b5) 샘플링.
- **프리뷰/Export 일치 원칙**: 셰이더와 pipeline.py 는 같은 단계·수식·계수를 유지해야 한다.
  한쪽 수정 시 반드시 양쪽 모두 수정. (export 공간반경 sigma는 full/proxy 비율로 스케일)

## 파일 구조

| 파일 | 역할 |
|------|------|
| `main.py` | 앱 진입점, 이미지 프로바이더(Raw/Lut/Curve), Controller(로드·WB·export) |
| `raw_loader.py` | RAF → 프록시 QImage (절대 Kelvin WB, half_size, max_edge=2560) |
| `wb.py` | Kelvin(+tint) → rawpy user_wb 배수, as-shot 색온도 추정 |
| `lut.py` | `.cube` 3D LUT 파서 → 2D 아틀라스(셰이더용) |
| `make_luts.py` | 근사 필름룩을 .cube 로 베이크(폴백용) |
| `pipeline.py` | **풀해상도 export** (numpy, 셰이더와 동일 파이프라인 재현) |
| `Main.qml` | 전체 UI (좌: 이미지 / 우: 스크롤 패널) |
| `CurveEditor.qml` | 톤 커브 위젯(드래그/추가/삭제, Catmull-Rom) |
| `shaders/adjust.frag` | 메인 파이프라인 프래그먼트 셰이더 |
| `shaders/blur.frag` | 분리형 가우시안 블러(로컬대비용) |
| `luts/*.cube` | 필름 시뮬레이션 LUT (abpy/FujifilmCameraProfiles sRGB, N=32) |

## 필름 시뮬레이션 LUT

- `luts/` 의 `.cube`(sRGB, N=32)는 abpy/FujifilmCameraProfiles 출처. 12종(identity, provia,
  velvia, astia, classic_chrome, classic_neg, nostalgic_neg, pro_neg_hi, pro_neg_std, eterna,
  reala_ace, bleach_bypass). 콤보 인덱스↔키 순서는 `Main.qml win.simKeys` 와 일치해야 함.
- 더 정확한 룩이 필요하면 **같은 키 이름으로 .cube 덮어쓰기**만 하면 됨(코드 변경 불필요, 크기
  자동 인식). 근사 베이크본 백업: `luts/_approx_backup/`.
- 주의: `make_luts.py` 실행 시 실제 LUT를 근사본으로 덮어쓴다.

## 조정 계수 (라이트룸 맞춤 튜닝 대상)

셰이더(`adjust.frag`)와 `pipeline.py` 양쪽에 동일하게 존재. 사용자 피드백으로 계속 조정한다.

| 도구 | 계수(현재) | 위치/비고 |
|------|-----------|-----------|
| Highlights/Shadows | **0.3** | tone_zones, 넓은 마스크 luma 오프셋 |
| Whites/Blacks | **0.3** | tone_zones, 끝단 좁은 마스크 |
| Texture | **1.6** | 작은반경 블러 하이패스 |
| Clarity | **0.8** | 큰반경 블러, 중간톤 가중 |
| Dehaze ⚠️**임시(톤모델)** | 로컬대비 **0.4** / 대비 **0.25** / 흰베일 **0.22** / 채도 **0.3** | + 안개걷힘 / − 흰베일로 밝게. 추후 물리 안개모델로 교체 예정 |
| Vignette | **0.8** | 방사형, − 가장자리 어둡게 |
| Temp/Tint | Planckian, TREF=5500 | 절대 Kelvin, 디코딩 단계 |

값을 바꿀 때는 **셰이더 + pipeline.py 동시 수정 + 셰이더 재컴파일** 후, `render_full` 로
응답 곡선(예: 평균밝기 vs 슬라이더)을 측정해 라이트룸과 비교한다.

## UI 규칙 / 주의사항

- 모든 슬라이더 **더블클릭 → 기본값 리셋**. Slider의 native `pressed` 신호로 더블프레스를 감지하고
  **release 시점에 리셋**(press 중에는 Slider가 value를 커서위치로 덮어쓰므로). `win.isDblPress()`.
  TapHandler 방식은 Slider grab과 충돌해 안 됨(과거 확인).
- 우측 패널은 **ScrollView**. **커브 에디터 높이는 반드시 고정값**(`Layout.preferredHeight: 240`).
  너비기반(정사각형)으로 두면 스크롤바↔availableWidth 레이아웃 루프로 창 전체가 느려짐(과거 버그).
- 커브 에디터 MouseArea는 `preventStealing: true`(ScrollView가 드래그 가로채는 것 방지).
- 셰이더 텍스처는 image provider 경로(Image→sampler)가 검증됨. Canvas→ShaderEffectSource 직접
  바인딩은 과거 검정화면 유발(커브 LUT를 provider 방식으로 전환해 해결).

## Export

- `pipeline.py` 가 풀해상도(6246×4170)를 동일 파이프라인으로 현상 → jpg/png/tif(8bit) 저장.
- **백그라운드 threading.Thread**(데몬)로 실행 → UI 안 멈춤. 26MP 전효과 ~40–50초(순수 CPU numpy,
  가우시안/LUT가 무거움). 메모리 위해 LUT 단계는 가로 스트립 처리, 공간단계는 전체 배열.
- 16bit TIFF 미지원(QImage 8bit). 필요 시 tifffile/imageio 추가.

## 향후 후보

**디헤이즈를 임시 톤 모델 → 물리 안개 모델(DCP/airlight 개선)로 정식 교체**(목표 우선순위),
Vibrance/Saturation, 채널별 커브(R/G/B), 프리셋 저장·불러오기, 16bit export, export 속도 최적화.

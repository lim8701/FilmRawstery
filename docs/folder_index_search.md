# 폴더 캡션 인덱싱 + 검색 (Florence-2)

폴더 안 사진들을 AI 캡션으로 색인해 **탐색기에서 단어로 검색**하는 기능. 기존 Florence-2
캡션(`caption.py`)을 재활용하며 신규 모델 없음. 브랜치 `feat/folder-index`.

## 개요 — 두 경로
1. **on-demand 캡션(GPU)**: 사진을 볼 때(캡션 오버레이 C on) 현재 상세도 캡션을 백그라운드 생성
   → 사이드카 저장 → 즉시 검색 대상. 빠름(짧은 캡션 ~1s, DirectML).
2. **폴더 배치 인덱싱(CPU)**: "⚙ Index" 버튼으로 폴더 전체를 백그라운드 색인 → 완전 검색 커버리지.

## 색공간·성능 (RTX 3050 Ti 실측)
- Florence-2는 **DirectML 가속 잘 됨**(SCUNet 케이스 아님): 비전 인코더 1307ms(CPU)→274ms(DML, 4.8×),
  디코더 스텝 310ms→141ms(2.2×). 짧은 캡션 장당 ~2.7s(CPU)→~1.05s(DML).
- **⚠️ 배치는 CPU 전용**: Florence-2를 GPU로 배치 추론하며 **동시에 다른 이미지를 로드하면**
  DirectML VRAM 초과로 **네이티브 크래시**(device removed). 그래서 배치는 `cpu=True`(별도
  `_state_cpu` CPU 세션), GPU는 프리뷰/편집 + on-demand 단일 캡션 전용으로 분리.
- **macOS**: provider 체인 DML/CoreML→CPU라 동작은 하나 CoreML 속도는 미검증(Mac 실측 필요).

## 파일별 변경
| 파일 | 내용 |
|------|------|
| `caption.py` | provider CPU고정 → **GPU EP 체인**(`_providers`: DML/CoreML→CPU, `ai_denoise_device.json` 디바이스 캐시 재사용). `generate(rgb, task, cpu=False)` + `_load_state(cpu)` — cpu=True면 CPU 전용 세션(`_state_cpu`). `provider_label()`. |
| `main.py` | 검색(`setSearchQuery`/`matchesSearch`/`searchQuery`), 커버리지(`indexedCount`/`photoCount`), 배치 인덱서(`startFolderIndex`/`cancelFolderIndex`/`_index_worker`/`_on_index_progress` + `indexBusy`/`indexProgress`/`indexDone`/`indexTotal`/`indexStatus`), `_caption_input_rgb`(임베드 프리뷰→768² RGB). |
| `hashtags.py` | `keywords(text, max_tags=0)` 신규(내용어 리스트, 불용어/숫자/3글자미만/중복 제거). `from_caption`이 이를 사용(표시 상한 15). |
| `ui/Main.qml` | 탐색기 검색창(TextInput + ✕ 삭제), 인덱싱 한 행([진행/커버리지 바][N/M][⚙/✕]), `explorerFiles` 검색 필터, `applySearch`(선택/스크롤 유지), `selectInExplorer(path, focus)`. |

## 사이드카
- 기존 캡션 사이드카 재사용: 폴더당 `.filmrawsterycaptions.json` = `{파일명: {short|detailed|paragraph: 문장}}`.
- 인덱싱은 **원문(캡션 문장) 저장** — 검색어/태그는 조회 시점에 파생(재인덱싱 없이 검색 규칙 변경 가능).
- 파일마다 atomic 저장 = 체크포인트(재개용).

## 검색 의미
- 대상 = 저장된 캡션(모든 상세도 합침)에서 **`hashtags.keywords()` 내용어**(불용어/숫자/3글자미만 제외
  — 표시 해시태그와 동일 규칙). **문장 전체가 아니라 내용어 기준.**
- 질의 토큰별 **접두(prefix) 일치**, 여러 토큰은 **AND**. 미인덱싱(캡션 없음) 파일은 검색 제외.
- 검색은 **무제한 내용어**(표시 해시태그는 상한 15 — 태그에 안 보여도 검색됨).
- 캐시는 `self._folder` 기준(경로 구분자 파싱 회피).

## 배치 인덱서 동작
- **대상 = 항상 폴더 전체**(`controller.fileList`). 검색어/좋아요 필터로 **좁히지 않음**(일관 동작 —
  검색 필터의 보이는 목록은 이미 인덱싱된 것뿐이라 대상 삼으면 전부 스킵됨).
- **show liked only면 좋아요된 사진을 먼저** 처리(시작 시점 정렬; 진행 중 토글은 재정렬 안 함).
- **재개**: 시작 시 이미 그 상세도 캡션이 있는 파일을 사전 필터로 제외 → 스킵에 대기·추론 0.
  (예: 500 중 300 완료 후 재실행 → 300 즉시 통과, 200만 처리.)
- **throttle(quiet)**: 파일 사이 `pace=0.4s` + 이미지 로드/익스포트/조작(`_busy`/`_exporting`/`_ui_busy`)
  중 일시정지(hold). UI 비블로킹.
- **취소**(✕): seq 증가로 다음 파일 경계에서 중단, busy 즉시 해제.
- **완료/취소 시 강제 재스캔**: 배치 중 우리 사이드카 저장이 watcher 재스캔을 억제(`_skip_rescan_once`)
  하므로, 끝나면 `_scan_folder`로 도중 추가된 파일을 목록/카운트에 반영.

## UI
- **검색창**: 파일 목록 위. 입력 시 필터(디바운스 180ms). 우측 **✕**로 삭제. Esc로 비움.
  텍스트 변경 시 선택 항목을 다시 선택+가운데 스크롤로 **선택/페이징 유지**(포커스는 검색창 유지).
- **인덱싱 한 행**: `[진행·커버리지 바][214/561][⚙/✕]`. 바=배치 중 진행률/유휴 시 커버리지 비율.
  카운트 N/M은 **한 곳에만**(중복 없음), 캡션 저장·배치 진행 시 실시간 갱신. 버튼 ⚙ 시작/✕ 취소.

## 상세도(레벨)와 검색 커버리지
- 인덱싱은 **현재 선택된 상세도 하나만** 추론(기본 Short). paragraph는 그 레벨로 조회/인덱싱해야 채워짐.
- 검색은 저장된 **모든 상세도**를 합쳐 대조 → 특정 사진을 paragraph로 조회하면 그 사진은 이후
  paragraph 단어로도 검색됨(사진별 누적).

## 커밋 (feat/folder-index, origin 반영)
```
4bca9d4  Show up to 15 caption hashtags
45389f0  Match search against caption content words (hashtag basis) instead of full text
f91dff4  Index whole folder regardless of list filter; prioritize liked when liked-only
19ea00a  Add background folder caption indexing (CPU) with resume, search, and coverage status
319f44d  Accelerate Florence-2 captioning on GPU (DirectML/CoreML) and add caption-based folder search
```
**dev 병합은 추가 확인 후 진행 예정.**

## 검증
- 헤드리스: QML 경고 0, 검색 매칭(접두·AND·내용어) 정확, 배치 재개(완료분 skip·generate 0회), CPU 세션
  동작(`EP=CPUExecutionProvider (cpu-forced/batch)`), 커버리지 카운트 정확.
- GUI(사용자 확인): GPU 캡션 스냅, 검색·✕·선택유지, 인덱싱 중 이미지 로드해도 크래시 없음, 재개 즉시.
- 실행: `.\.venv\Scripts\python.exe main.py`.

## 주의/한계
- 배치 CPU라 GPU보다 느림(장당 warm ~2.7s → 999장 ~45분+). 백그라운드·재개라 실사용 무난, quiet로 발열 완화.
- macOS CoreML 속도 미검증.
- 검색은 영어 내용어 기준(캡션이 영어 전용).

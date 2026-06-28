# 해결됨: 프리뷰 ≠ Export 색 차이 (원인=광색역 모니터 + 앱 비색관리)

## 결론 (2026-06 측정으로 확정)
- **렌더 픽셀은 프리뷰·CPU export·GPU export 가 전부 동일**하다(버그 아님). 셰이더 `adjust.frag`
  출력 == `pipeline.render_full`(numpy) == 화면 합성(스크린샷)까지 모두 일치.
- 차이는 **표시 단계**였다: 노트북 패널이 **광색역(XPS 9520 OLED ≈ DCI-P3)** 인데 Qt 프리뷰가
  **색 관리를 안 해서**, sRGB 값이 P3 패널에 그대로 나가 과포화·따뜻하게 보였다. export 파일을
  색관리되는 뷰어로 보면 sRGB→패널로 변환돼 정확(차분)하게 나오므로 "프리뷰가 더 붉다"로 느껴졌다.

## 측정 근거 (디버그 grab + numpy 비교, 동일 상태)
- `grab(pipe)` R/B 1.0398 ≈ `render_full`(CPU export) 1.0397 ≈ `grab(pipeView)` 1.0399 — 픽셀 동일.
- 스크린샷 이미지영역(중성 매트) R/B 1.2162 ≈ `grab(cropClip)` 1.2148 — 화면 합성도 픽셀 불변.
- 즉 OS 합성/스왑체인도 색을 안 틀었고, 남은 변수는 **패널 색역**뿐이었다.

## 해결 (프리뷰 전용 디스플레이 색관리)
- `display_cm.py`: 현재 모니터 **ICC 프로파일**(Windows `GetICMProfile`)을 읽어
  `QColorSpace`/`QImage.applyColorTransform` 으로 **sRGB→디스플레이 3D LUT 아틀라스**를 굽는다
  (필름시뮬 LUT 와 동일 포맷·트라이리니어 재사용). sRGB 모니터/프로파일 없음이면 항등(무영향).
- `adjust.frag`: 최종 단계에 `displayCM`(uniform) + `cmLut`(binding 10) 트라이리니어 적용.
  **프리뷰(`pipe`)만 적용**, **export(`pipeFull`/`render_full`)는 미적용**(표준 sRGB 유지 — 다른 PC 공유 안전).
- `main.py`: `DisplayCmProvider` + 시작/`screenChanged` 시 `refreshDisplayCm`(모니터별 자동 재생성).
- UI: 우측 패널 "Display color management" 체크박스(광색역 모니터에서만 노출) + **Ctrl+Shift+M** 토글.
  기본 ON. `win.displayCM && controller.hasDisplayCM` 일 때만 셰이더에서 적용.

## 한계 / 메모
- `Compare original`(\\) 모드도 CM 적용됨: `dispPre`(블러 base, sRGB 유지)는 그대로 두고,
  CM 전용 패스 `comparePipe`(`shaders/displaycm.frag`)가 dispPre 에 CM 만 입혀 표시(before/after 일관).
- Windows "자동 색 관리(ACM)" 를 켜면 이중 보정 → 앱 토글과 둘 중 하나만 사용.
- 정확도는 모니터 ICC 프로파일 품질에 의존(XPS 기본 `Final_ICM_..._SHP1516.icm` 로 검증, 완전 일치).

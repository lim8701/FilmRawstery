"""현상 계수 단일 진실원 — 셰이더(uniform 주입)와 pipeline.py(numpy)가 공유한다.

여기 값을 바꾸면 프리뷰(GPU 셰이더)와 CPU export(pipeline.py) 양쪽에 동시 반영된다
(예전엔 셰이더 리터럴 ↔ pipeline 리터럴을 따로 고쳐야 했고, 한쪽을 빠뜨리면 프리뷰≠export).
계수 변경 시 셰이더 재컴파일도 불필요(uniform 주입) — 라이트룸 비교 튜닝 반복이 빨라짐.

⚠️ 로컬대비/디헤이즈/하늘 WB 계열만 단일화했다. 전역 톤(Highlights/Shadows 1.0, Whites/Blacks
0.3, Vignette 0.8, Grain 0.12 등)은 아직 셰이더·pipeline 리터럴로 중복 — 추후 확장 가능.
"""

# 디헤이즈 톤모델(임시 — CLAUDE.md: 추후 물리 안개모델로 교체 예정). 셰이더 dehazeTone == pipeline._dehaze_core.
DEHAZE_LOCAL = 0.4      # 로컬대비 가산
DEHAZE_CONTRAST = 0.25  # 대비
DEHAZE_VEIL = 0.22      # 흰 베일(amt<0, 밝아짐)
DEHAZE_SAT = 0.3        # 채도

CLARITY = 0.8           # 클래리티(중간톤 로컬대비)
TEXTURE = 1.6           # 텍스처(중주파)

SKY_TEMP = 0.20         # 하늘 색온도 채널 게인
SKY_TINT = 0.15         # 하늘 틴트(녹-마젠타) 채널 게인


def as_qml_dict():
    """QML ShaderEffect uniform 바인딩용 (controller.adjustCoeffs). 셰이더 uniform 이름과 일치."""
    return {
        "dehazeKLocal": DEHAZE_LOCAL, "dehazeKContrast": DEHAZE_CONTRAST,
        "dehazeKVeil": DEHAZE_VEIL, "dehazeKSat": DEHAZE_SAT,
        "clarityK": CLARITY, "textureK": TEXTURE,
        "skyTempK": SKY_TEMP, "skyTintK": SKY_TINT,
    }

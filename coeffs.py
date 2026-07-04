"""현상 계수 단일 진실원 — 셰이더(uniform 주입)와 pipeline.py(numpy)가 공유한다.

여기 값을 바꾸면 프리뷰(GPU 셰이더)와 CPU export(pipeline.py) 양쪽에 동시 반영된다
(예전엔 셰이더 리터럴 ↔ pipeline 리터럴을 따로 고쳐야 했고, 한쪽을 빠뜨리면 프리뷰≠export).
계수 변경 시 셰이더 재컴파일도 불필요(uniform 주입) — 라이트룸 비교 튜닝 반복이 빨라짐.

⚠️ 로컬대비/디헤이즈/하늘 WB 계열만 단일화했다. 전역 톤(Highlights/Shadows 1.0, Whites/Blacks
0.3, Vignette 0.8, Grain 0.12 등)은 아직 셰이더·pipeline 리터럴로 중복 — 추후 확장 가능.
"""

# 디헤이즈 톤모델 — '−'(흰 베일) 방향 + 물리 모델 폴백(어두운 장면)용. 셰이더 dehazeTone == pipeline._dehaze_core.
DEHAZE_LOCAL = 0.4      # 로컬대비 가산
DEHAZE_CONTRAST = 0.25  # 대비
DEHAZE_VEIL = 0.22      # 흰 베일(amt<0, 밝아짐)
DEHAZE_SAT = 0.3        # 채도

# 디헤이즈 물리 모델(DCP, '+' 방향 — haze.py 가 이미지당 t-맵/대기광/conf 추정). 셰이더 6단계 == pipeline._dehaze.
DEHAZE_TMIN = 0.15      # 유효 투과율 하한(짙은 안개서 0-나눗셈/노이즈 증폭 방지)
DEHAZE_RESID = 0.35     # 물리 복원 위에 남기는 톤모델 비율(라이트룸 체감의 대비/채도 '펀치' 보정)

CLARITY = 0.8           # 클래리티(중간톤 로컬대비)
TEXTURE = 1.6           # 텍스처(중주파)

SKY_TEMP = 0.20         # 하늘 색온도 채널 게인
SKY_TINT = 0.15         # 하늘 틴트(녹-마젠타) 채널 게인

# 전역 톤(tone_zones / 비네팅 / 그레인). 셰이더 tone_zones·10·12단계 == pipeline._tone_zones / render_full.
TONE_HISH = 1.0         # Highlights/Shadows 국소 노출 stop 스케일
TONE_WHBL = 0.3         # Whites/Blacks 끝단 레벨 이동
VIGNETTE = 0.8          # 비네팅 방사 강도
GRAIN = 0.12            # 필름 그레인 강도

# 기타 강도 계수 (샤프닝 / HSL 믹서 / 컬러 그레이딩)
SHARPEN = 1.5           # 언샤프 마스크 강도
HSL_HUE_DEG = 30.0      # HSL 색상대 hue 시프트 최대(도)
HSL_LUM = 0.5           # HSL 휘도 조정 스케일
COLOR_GRADE = 0.5       # 컬러 그레이딩(스플릿 토닝) 강도


def as_qml_dict():
    """QML ShaderEffect uniform 바인딩용 (controller.adjustCoeffs). 셰이더 uniform 이름과 일치."""
    return {
        "dehazeKLocal": DEHAZE_LOCAL, "dehazeKContrast": DEHAZE_CONTRAST,
        "dehazeKVeil": DEHAZE_VEIL, "dehazeKSat": DEHAZE_SAT,
        "dehazeKTmin": DEHAZE_TMIN, "dehazeKResid": DEHAZE_RESID,
        "clarityK": CLARITY, "textureK": TEXTURE,
        "skyTempK": SKY_TEMP, "skyTintK": SKY_TINT,
        "toneHiShK": TONE_HISH, "toneWhBlK": TONE_WHBL,
        "vignetteK": VIGNETTE, "grainK": GRAIN,
        "sharpenK": SHARPEN, "hslHueDegK": HSL_HUE_DEG,
        "hslLumK": HSL_LUM, "colorGradeK": COLOR_GRADE,
    }

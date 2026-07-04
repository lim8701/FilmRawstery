"""디헤이즈 물리 모델(DCP) — 투과율 맵 + 대기광 추정. PySide6/QML 비의존(numpy in/out).

안개 모델 I = J·t + A·(1−t) (He et al., Dark Channel Prior):
  - dark channel: 맑은 장면의 국소 최소 채널은 ≈0, 안개는 모든 채널을 A 쪽으로 들어올려 >0.
  - 대기광 A: dark channel 상위 0.1% 픽셀(가장 안개 짙은 곳) 중 밝은 픽셀들의 평균색.
  - 투과율 t = 1 − ω·dark(I/A). ω<1 로 원거리 잔안개를 남겨 자연스러움 유지.
  - guided filter 로 t 를 휘도 엣지에 밀착(깊이 경계 헤일로 방지) — sky_seg 와 동일 기법.

사용처: 이미지 로드 시 1회 추정(중성 display sRGB 베이스) → 프리뷰는 t-맵 텍스처+A/conf
uniform, export 는 t-맵 업샘플로 동일 수식 적용(프리뷰=Export 정합). 복원 수식 자체는
셰이더 adjust.frag(6단계)와 pipeline._dehaze 가 공유 계수(coeffs.py)로 각각 구현한다.

conf(신뢰도): 대기광이 어둡거나 장면 전체가 어두우면 A-추정이 무의미(야경 등) → 0 에
가까워지고, 소비측은 conf 로 물리↔톤모델을 블렌드(어두운 장면은 기존 라이트룸 체감 유지).
"""

import numpy as np
from scipy.ndimage import minimum_filter

from sky_seg import _guided_filter

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

OMEGA = 0.95        # 걷힘 상한(1=완전 제거 — 원거리 잔안개를 남겨야 자연스러움)
PATCH_FRAC = 1 / 24  # dark channel 패치 반경(긴 변 대비) — 너무 작으면 노이즈, 크면 헤일로


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def estimate(disp: np.ndarray, long_edge: int = 512):
    """중성 display sRGB(float32, 0..1, (H,W,3))에서 (t, A, conf) 추정.

    반환: t=투과율 맵(float32, 소형 해상도, 0..1 — 1=안개 없음), A=대기광 RGB(float32 3,),
          conf=추정 신뢰도(0..1 — 0=물리 모델 쓰지 말 것).
    입력은 노출0·as-shot WB 베이스여야 슬라이더 조작과 무관하게 이미지당 1회로 안정."""
    h, w = disp.shape[:2]
    step = max(1, int(round(max(h, w) / float(long_edge))))
    d = np.ascontiguousarray(disp[::step, ::step]).astype(np.float32)
    dh, dw = d.shape[:2]
    r = max(2, int(round(max(dh, dw) * PATCH_FRAC * 0.5)))
    size = 2 * r + 1

    # 대기광 A: dark channel 상위 0.1%(가장 안개 짙은 후보) 중 밝은 픽셀 평균색
    dark0 = minimum_filter(d.min(axis=2), size=size)
    n = dark0.size
    k = max(1, int(n * 0.001))
    idx = np.argpartition(dark0.ravel(), n - k)[n - k:]
    cand = d.reshape(-1, 3)[idx]
    lum = cand @ LUMA
    top = cand[np.argsort(lum)[-max(1, k // 4):]]
    A = np.clip(top.mean(axis=0), 0.05, 1.0).astype(np.float32)

    # 투과율: t = 1 − ω·dark(I/A) → guided filter 로 휘도 엣지에 정제
    t = 1.0 - OMEGA * minimum_filter((d / A[None, None, :]).min(axis=2), size=size)
    t = np.clip(t, 0.0, 1.0).astype(np.float32)
    guide = (d @ LUMA).astype(np.float32)
    t = _guided_filter(guide, t, radius=2 * r, eps=1e-3)
    t = np.clip(t, 0.0, 1.0).astype(np.float32)

    # 신뢰도: 대기광이 어둡거나(야경 — A 추정 파탄) 장면이 전반적으로 어두우면 0 → 톤모델 폴백
    conf = (_smoothstep(0.30, 0.55, float(A @ LUMA))
            * _smoothstep(0.06, 0.15, float(guide.mean())))
    return t, A, float(conf)

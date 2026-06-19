"""X100V 고정렌즈(23mm) 프로파일 보정 — 왜곡 / 주변광량 / 색수차(CA).

디코딩 직후(프록시: raw_loader, export: pipeline)에 numpy 로 적용해 전체 파이프라인이
보정된 이미지를 사용하게 한다. 정규화 반경 기반이라 해상도 무관(프리뷰=export 동일).

⚠️ 계수는 근사 출발값이며 튜닝 대상이다(측정 프로파일 미보유):
  - k1<0 = 배럴 보정. 직선이 반대로 휘면 부호를 뒤집고, 양이 모자라면 키운다.
  - vig = 코너 게인(주변광량 회복). ca_* = 채널 방사 배율(색수차).
"""
import numpy as np
from scipy.ndimage import map_coordinates

# X100V 23mm 프로파일 (근사, 튜닝 대상)
X100V = {
    "k1": -0.030,    # 배럴 왜곡 보정(음수=배럴)
    "k2": 0.000,     # 2차 항(미세)
    "ca_r": 0.0008,  # 적색 채널 방사 배율 차(CA)
    "ca_b": -0.0010,  # 청색 채널
    "vig": 0.28,     # 주변광량 보정 코너 게인(1+vig)
}


def apply(arr, p=X100V):
    """arr (H,W,3) uint8/uint16/float → 보정본(같은 dtype). 정규화 반경 기반."""
    h, w = arr.shape[:2]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    norm2 = cx * cx + cy * cy
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = xs - cx
    dy = ys - cy
    rn2 = (dx * dx + dy * dy) / norm2                 # 0(중심)..1(코너)
    s = 1.0 + p["k1"] * rn2 + p["k2"] * rn2 * rn2     # 왜곡 리맵 배율

    out = np.empty_like(arr)
    ca = (1.0 + p["ca_r"], 1.0, 1.0 + p["ca_b"])      # R, G, B 채널 배율(CA)
    for ch in range(3):
        sc = s * ca[ch]
        coords = [cy + dy * sc, cx + dx * sc]          # 소스 샘플 좌표
        out[..., ch] = map_coordinates(arr[..., ch], coords, order=1, mode="nearest")

    # 주변광량 보정(코너 밝힘)
    if p["vig"] != 0.0:
        gain = (1.0 + p["vig"] * rn2)[..., None]
        o = out.astype(np.float32) * gain
        if np.issubdtype(arr.dtype, np.integer):
            o = np.clip(o, 0, np.iinfo(arr.dtype).max)
            out = o.astype(arr.dtype)
        else:
            out = np.clip(o, 0.0, 1.0).astype(arr.dtype)
    return out

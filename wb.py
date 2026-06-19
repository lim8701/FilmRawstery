"""절대 색온도(Kelvin) -> 카메라 RAW 화이트밸런스 배수(user_wb).

rawpy 의 카메라 색매트릭스(rgb_xyz_matrix)와 카메라가 보정해 둔
daylight_whitebalance 에 앵커링한다. TREF(데이라이트 기준 색온도)에서
daylight_whitebalance 가 그대로 나오고, 그로부터 Planckian locus 비율로
스케일해 임의의 Kelvin 을 만든다.
"""

import numpy as np

TREF = 5500  # daylight_whitebalance 가 중립이 되는 기준 색온도(K)


def planckian_xy(T: float):
    """색온도 T(K) -> CIE xy 색도 (Planckian locus 근사)."""
    T = float(T)
    if T < 4000:
        x = (-0.2661239e9 / T**3 - 0.2343589e6 / T**2
             + 0.8776956e3 / T + 0.179910)
    else:
        x = (-3.0258469e9 / T**3 + 2.1070379e6 / T**2
             + 0.2226347e3 / T + 0.240390)
    if T < 2222:
        y = -1.1063814 * x**3 - 1.34811020 * x**2 + 2.18555832 * x - 0.20219683
    elif T < 4000:
        y = -0.9549476 * x**3 - 1.37418593 * x**2 + 2.09137015 * x - 0.16748867
    else:
        y = 3.0817580 * x**3 - 5.87338670 * x**2 + 3.75112997 * x - 0.37001483
    return x, y


def _planck_cam(cam_xyz: np.ndarray, T: float) -> np.ndarray:
    """색온도 T 백색의 카메라 native RGB 응답."""
    x, y = planckian_xy(T)
    XYZ = np.array([x / y, 1.0, (1.0 - x - y) / y])
    return np.clip(cam_xyz @ XYZ, 1e-6, None)


def compute_user_wb(cam_xyz, daylight_ref, kelvin, tint: float = 0.0):
    """Kelvin(+tint) -> rawpy user_wb 배수 [R, G, B, G2].

    tint: + 마젠타(녹↓) / - 그린(녹↑)
    """
    cam_xyz = np.asarray(cam_xyz, float)
    gain = _planck_cam(cam_xyz, TREF) / _planck_cam(cam_xyz, kelvin)
    m = np.asarray(daylight_ref, float) * gain
    m = m / m[1]                  # green 정규화
    m[1] *= (1.0 - 0.3 * tint)    # tint 적용
    return [float(m[0]), float(m[1]), float(m[2]), float(m[1])]


def estimate_cct(cam_xyz, daylight_ref, camera_wb) -> int:
    """카메라 as-shot WB 배수에 가장 가까운 Kelvin 을 탐색해 반환."""
    cw = np.asarray(camera_wb, float)[:3]
    cw = cw / cw[1]
    best_t, best_d = TREF, 1e18
    for T in range(2000, 12001, 50):
        m = np.asarray(compute_user_wb(cam_xyz, daylight_ref, T)[:3])
        d = float(np.sum((m - cw) ** 2))
        if d < best_d:
            best_t, best_d = T, d
    return best_t

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


# 선형 sRGB -> XYZ (D65) 표준 매트릭스 (dcraw xyz_rgb 와 동일)
_XYZ_RGB_D65 = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])


def cam_to_srgb_matrix(cam_xyz):
    """카메라 네이티브 RGB -> 선형 sRGB 3x3 매트릭스 (dcraw/LibRaw 방식).

    cam_xyz 는 XYZ->cameraRGB (wb.py 전반에서 `cam_xyz @ XYZ` 로 사용).
    dcraw `cam_xyz_coeff`: cam_rgb = cam_xyz @ (sRGB->XYZ) [=sRGB->cam], 각 행을
    행합=1 로 정규화(=pre_mul, 화이트 D65->중성), 그 역행렬이 cam->sRGB(linear).
    rawpy output_color=sRGB 의 매트릭스와 일치(잔차 ~1/255 는 rawpy 독자 비선형
    색렌더이며 매트릭스로 재현 불가 — 표준 colorimetry 를 따른다).
    """
    cam_xyz = np.asarray(cam_xyz, float).reshape(3, 3)
    cam_rgb = cam_xyz @ _XYZ_RGB_D65                       # sRGB -> cam
    cam_rgb = cam_rgb / cam_rgb.sum(axis=1, keepdims=True)  # 행합=1 (pre_mul)
    return np.linalg.inv(cam_rgb)                          # cam -> sRGB(linear)


def baked_wb(cam_xyz, daylight_ref):
    """프록시 디코딩에 베이크할 TREF(daylight) 기준 WB 배수."""
    return compute_user_wb(cam_xyz, daylight_ref, TREF, 0.0)


def srgb_to_linear(c):
    """sRGB(또는 rawpy gamma=(2.4,12.92)) -> 선형. 셰이더 srgbToLinear 와 정합."""
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    """선형 -> sRGB. 셰이더 linearToSrgb 와 정합."""
    c = np.clip(np.asarray(c, float), 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1.0 / 2.4)) - 0.055)


def rel_gain(cam_xyz, daylight_ref, kelvin, tint=0.0):
    """TREF 베이크 대비 상대 WB 게인(카메라공간, green 정규화). 셰이더 wbPreview 와 동일."""
    t = np.asarray(compute_user_wb(cam_xyz, daylight_ref, kelvin, tint)[:3], float)
    b = np.asarray(baked_wb(cam_xyz, daylight_ref)[:3], float)
    g = t / b
    g = g / g[1]
    return g


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

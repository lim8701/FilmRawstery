"""RAF -> 화면 표시용 프록시 QImage 디코더.

편집은 축소된 프록시로 하고(인터랙티브용), 풀해상도는 나중에 export 단계에서만
처리한다. 화이트밸런스는 절대 색온도(Kelvin)로 디코딩 단계에서 적용한다.
"""

import numpy as np
import rawpy
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

import lens
from wb import compute_user_wb, estimate_cct


def load_proxy(path: str, kelvin=None, tint: float = 0.0, max_edge: int = 2560,
               lens_correct: bool = True):
    """RAF 를 디코딩해 (QImage, as_shot_kelvin, cam_xyz(3x3 list), ref(3 list)) 반환.

    kelvin=None 이면 카메라 as-shot 추정 색온도로 디코딩한다.
    cam_xyz/ref 는 QML 실시간 WB 프리뷰 게인 계산에 사용한다.
    """
    with rawpy.imread(path) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix)[:3, :3]
        ref = np.array(raw.daylight_whitebalance)[:3]
        ref = ref / ref[1]
        as_shot = estimate_cct(cam_xyz, ref, raw.camera_whitebalance)

        k = as_shot if kelvin is None else kelvin
        user_wb = compute_user_wb(cam_xyz, ref, k, tint)

        rgb = raw.postprocess(
            user_wb=user_wb,       # 절대 색온도 기반 WB
            half_size=True,        # 빠른 디코딩(프록시 용도)
            output_bps=8,
            no_auto_bright=True,   # 자동 밝기 보정 OFF (LUT가 톤을 책임짐)
            gamma=(2.4, 12.92),    # 표준 sRGB EOTF (LibRaw 기본 2.222/4.5는 섀도를 더 눌러 대비↑)
        )

    if lens_correct:
        rgb = lens.apply(rgb)          # X100V 렌즈 프로파일(왜곡/주변광량/CA)
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

    if max(w, h) > max_edge:
        img = img.scaled(
            max_edge, max_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return img, int(as_shot), cam_xyz.flatten().tolist(), ref.tolist()

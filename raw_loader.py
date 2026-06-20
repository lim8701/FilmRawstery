"""RAF -> 화면 표시용 프록시 QImage 디코더.

편집은 축소된 프록시로 하고(인터랙티브용), 풀해상도는 나중에 export 단계에서만
처리한다. 화이트밸런스는 절대 색온도(Kelvin)로 디코딩 단계에서 적용한다.
"""

import numpy as np
import rawpy
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

import lens
from wb import baked_wb, cam_to_srgb_matrix, estimate_cct


def load_proxy(path: str, kelvin=None, tint: float = 0.0, max_edge: int = 2560,
               lens_correct: bool = True):
    """RAF 를 디코딩해 (QImage, as_shot, cam_xyz(9), ref(3), cam2srgb(9)) 반환.

    WB 는 더 이상 디코딩에 베이크하지 않는다(셰이더가 카메라공간에서 실시간 적용).
    프록시는 **카메라 네이티브 RGB**(매트릭스 미적용)를 TREF(daylight) WB 만 베이크해
    감마 인코딩(8bit)으로 저장. 셰이더가 [선형화→WB 상대게인→cam2srgb 매트릭스→sRGB]
    로 변환한다. kelvin/tint 인자는 호출부 호환용으로 유지하되 미사용.
    """
    with rawpy.imread(path) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix)[:3, :3]
        ref = np.array(raw.daylight_whitebalance)[:3]
        ref = ref / ref[1]
        as_shot = estimate_cct(cam_xyz, ref, raw.camera_whitebalance)

        rgb = raw.postprocess(
            user_wb=baked_wb(cam_xyz, ref),     # TREF daylight 베이크(고정)
            output_color=rawpy.ColorSpace.raw,  # 카메라 네이티브(매트릭스 미적용)
            half_size=True,                     # 빠른 디코딩(프록시 용도)
            output_bps=8,
            no_auto_bright=True,                # 자동 밝기 보정 OFF
            gamma=(2.4, 12.92),                 # 감마 인코딩 저장(8bit 정밀도)
            highlight_mode=rawpy.HighlightMode.Clip,
        )

    if lens_correct:
        rgb = lens.apply(rgb)          # X100V 렌즈 프로파일(왜곡/주변광량/CA), 색공간 무관
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

    if max(w, h) > max_edge:
        img = img.scaled(
            max_edge, max_edge,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), cam_xyz.flatten().tolist(), ref.tolist(),
            cam2srgb.flatten().tolist())

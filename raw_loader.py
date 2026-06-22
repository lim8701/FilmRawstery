"""RAF -> 화면 표시용 프록시 QImage 디코더.

편집은 축소된 프록시로 하고(인터랙티브용), 풀해상도는 나중에 export 단계에서만
처리한다. 화이트밸런스는 절대 색온도(Kelvin)로 디코딩 단계에서 적용한다.
"""

import numpy as np
import rawpy
from scipy.ndimage import zoom
from PySide6.QtGui import QImage

import lens
from wb import (auto_exposure_gain, baked_wb, cam_to_srgb_matrix, estimate_wb,
                linear_to_srgb, srgb_to_linear)

# 프리뷰 프록시 헤드룸: scene-linear 를 8bit 에 담을 때 code=oetf(L/H), 셰이더가 ×H 로 복원.
# H 만큼(여기 4× ≈ 2스톱) 하이라이트 헤드룸 확보 → filmic 톤커브가 누를 여지 보존.
# ⚠️adjust.frag/convert.frag 의 PROXY_HEADROOM 와 반드시 동일해야 함.
PROXY_HEADROOM = 4.0

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _embedded_jpeg_lum(raw):
    """RAF 임베드 JPEG(카메라 현상본)의 휘도(0..1) 1D 배열. 실패 시 None.
    이미지별 자동 노출의 '목표 밝기'(카메라의 샷별 측광/톤 의도)를 통계로 쓴다."""
    try:
        th = raw.extract_thumb()
        if th.format != rawpy.ThumbFormat.JPEG:
            return None
        qi = QImage()
        if not qi.loadFromData(th.data):
            return None
        qi = qi.convertToFormat(QImage.Format.Format_RGB888)
        w, h = qi.width(), qi.height()
        if w == 0 or h == 0:
            return None
        a = (np.frombuffer(qi.constBits(), np.uint8)
             .reshape(h, qi.bytesPerLine())[:, :w * 3].reshape(h, w, 3).astype(np.float32) / 255.0)
        return (a @ LUMA).ravel()
    except Exception:
        return None


def _embedded_jpeg_median(raw):
    """임베드 JPEG 중앙값 휘도(0..1). 고휘도(하늘 등)에 강건 → scene-linear 자동노출 목표."""
    lum = _embedded_jpeg_lum(raw)
    return None if lum is None else float(np.median(lum))


# 감마 변환 LUT(pow 대신 게더). wb 의 정확 함수로 1회 구축 → 값 동일, 속도만 향상.
_SRGB2LIN = None
_LIN2SRGB = None


def _dither(shape):
    """사각형 디더(±0.5 LSB) — 8bit 양자화 밴딩 제거(확률적 반올림). 시드 고정(재로드 동일)."""
    return np.random.default_rng(12345).random(shape, dtype=np.float32) - 0.5


def _srgb2lin_lut():
    global _SRGB2LIN
    if _SRGB2LIN is None:
        _SRGB2LIN = srgb_to_linear(np.arange(65536, dtype=np.float32) / 65535.0).astype(np.float32)
    return _SRGB2LIN


def _lin2srgb_lut():
    global _LIN2SRGB
    if _LIN2SRGB is None:
        _LIN2SRGB = linear_to_srgb(np.arange(65536, dtype=np.float32) / 65535.0).astype(np.float32)
    return _LIN2SRGB


def _decode_native(path: str):
    """RAF -> 카메라네이티브 16bit(TREF 베이크, 매트릭스 미적용) + 메타. load_proxy/load_full 공용.

    WB 는 디코딩에 베이크하지 않는다(셰이더가 카메라공간 상대게인으로 실시간 적용). TREF(daylight)만
    베이크. X-Trans 는 half_size 격자 회피를 위해 full + LINEAR 디모자이크.
    반환: (rgb16, cam_xyz(3x3), ref(3), as_shot, as_shot_tint, target_median)
    """
    with rawpy.imread(path) as raw:
        cam_xyz = np.array(raw.rgb_xyz_matrix)[:3, :3]
        ref = np.array(raw.daylight_whitebalance)[:3]
        ref = ref / ref[1]
        as_shot, as_shot_tint = estimate_wb(cam_xyz, ref, raw.camera_whitebalance)
        target_median = _embedded_jpeg_median(raw)   # 이미지별 자동 노출 목표(중앙값, 고휘도 강건)
        rgb16 = raw.postprocess(
            user_wb=baked_wb(cam_xyz, ref),     # TREF daylight 베이크(고정)
            output_color=rawpy.ColorSpace.raw,  # 카메라 네이티브(매트릭스 미적용)
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            output_bps=16,                      # 게인/감마를 numpy 로 적용
            no_auto_bright=True,                # 자동 밝기 보정 OFF(상대노출 보존)
            gamma=(2.4, 12.92),                 # 감마 인코딩
            highlight_mode=rawpy.HighlightMode.Clip,
        )
    return rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median


def _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, lens_correct):
    """카메라네이티브 16bit -> (렌즈) -> 선형 -> 자동노출 -> 헤드룸 인코딩(disp float[0,1]).

    code = oetf(L/H): scene-linear L 을 H 로 나눠 [0,1] 감마로 인코딩(셰이더가 ×H 복원).
    ⚠️렌즈 보정을 현상 전 카메라네이티브에 먼저 적용해야 export(render_full)와 정합(자동노출이
      렌즈 적용 후 통계로 계산됨). load_proxy(8bit)·load_full(16bit) 공용 — 동일 scene-linear 보장.
    """
    if lens_correct:
        rgb16 = np.clip(lens.apply(rgb16), 0.0, 65535.0).astype(np.uint16)
    lin = _srgb2lin_lut()[rgb16]                         # (H,W,3) float32 선형(카메라네이티브)
    lin *= auto_exposure_gain(target_median, cam_xyz, ref, as_shot, lin)
    idx = (np.clip(lin * (1.0 / PROXY_HEADROOM), 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    return _lin2srgb_lut()[idx]                          # float32 [0,1] 헤드룸 인코딩(카메라네이티브)


def load_proxy(path: str, max_edge: int = 2560, lens_correct: bool = True):
    """RAF 를 디코딩해 (QImage(8bit), as_shot, as_shot_tint, cam_xyz(9), ref(3), cam2srgb(9)) 반환.

    프록시는 카메라 네이티브 RGB(매트릭스 미적용)를 TREF WB 베이크 + 헤드룸 감마 인코딩(8bit).
    셰이더가 [선형화→WB 상대게인→cam2srgb 매트릭스→filmic] 로 변환한다.
    """
    rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median = _decode_native(path)

    # full 디코딩(X-Trans 격자 회피)이라 무거운 numpy 전에 먼저 max_edge 로 축소(반응성).
    # 정수 2× 박스평균(빠른 AA) 반복 후 남은 분수배만 가벼운 bilinear zoom.
    if max(rgb16.shape[:2]) > max_edge:
        x = rgb16.astype(np.float32)
        while max(x.shape[0] // 2, x.shape[1] // 2) >= max_edge and min(x.shape[:2]) >= 2:
            hh, ww = (x.shape[0] // 2) * 2, (x.shape[1] // 2) * 2
            x = (x[0:hh:2, 0:ww:2] + x[1:hh:2, 0:ww:2]
                 + x[0:hh:2, 1:ww:2] + x[1:hh:2, 1:ww:2]) * 0.25
        f = max_edge / float(max(x.shape[:2]))
        if f < 1.0:
            x = zoom(x, (f, f, 1.0), order=1)
        rgb16 = np.clip(x + 0.5, 0.0, 65535.0).astype(np.uint16)

    disp = _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, lens_correct)
    dth = _dither(disp.shape)               # ±0.5 LSB 디더(8bit 양자화 밴딩 제거)
    rgb = np.clip(disp * 255.0 + 0.5 + dth, 0.0, 255.0).astype(np.uint8)
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), float(as_shot_tint), cam_xyz.flatten().tolist(),
            ref.tolist(), cam2srgb.flatten().tolist())


def load_full(path: str, lens_correct: bool = True):
    """GPU export 용: 다운스케일 없는 풀해상도 + 16bit(RGBA64) 헤드룸 인코딩. 메타는 load_proxy 와 동형.

    프록시(8bit, 프리뷰용)와 동일 인코딩 규약(셰이더 src 입력)이되 다운스케일 없음 + 16bit.
    """
    rgb16, cam_xyz, ref, as_shot, as_shot_tint, target_median = _decode_native(path)
    disp = _encode_headroom(rgb16, cam_xyz, ref, as_shot, target_median, lens_correct)
    code = (np.clip(disp, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    h, w, _ = code.shape
    rgba = np.empty((h, w, 4), np.uint16)
    rgba[..., :3] = code
    rgba[..., 3] = 65535                     # alpha=불투명(RGBA64 포맷)
    rgba = np.ascontiguousarray(rgba)
    img = QImage(rgba.data, w, h, 8 * w, QImage.Format.Format_RGBA64).copy()
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), float(as_shot_tint), cam_xyz.flatten().tolist(),
            ref.tolist(), cam2srgb.flatten().tolist())

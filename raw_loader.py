"""RAF -> 화면 표시용 프록시 QImage 디코더.

편집은 축소된 프록시로 하고(인터랙티브용), 풀해상도는 나중에 export 단계에서만
처리한다. 화이트밸런스는 절대 색온도(Kelvin)로 디코딩 단계에서 적용한다.
"""

import math

import numpy as np
import rawpy
from scipy.ndimage import zoom
from PySide6.QtGui import QImage

import lens
from wb import (baked_wb, cam_to_srgb_matrix, estimate_wb,
                linear_to_srgb, rel_gain, srgb_to_linear)

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# 디코딩 베이스라인 노출 폴백 게인 (임베드 JPEG 를 못 읽을 때만 사용).
# RAF 의 no_auto_bright 선형 디코딩은 카메라 JPEG 보다 어둡다(화이트포인트가 상위 레인지를
# 안 씀). 보통은 아래 solve_baseline_gain 이 임베드 JPEG 밝기에 맞춰 이미지별 게인을 구한다.
BASELINE_GAIN = 4.0


def _embedded_jpeg_mean(raw):
    """RAF 임베드 JPEG(카메라 현상본)의 평균 휘도(0..1). 실패 시 None.
    이미지별 자동 베이스라인 노출의 '목표 밝기'(카메라의 샷별 측광/톤 의도)."""
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
        return float((a @ LUMA).mean())
    except Exception:
        return None


# 감마 변환 LUT(pow 대신 게더). wb 의 정확 함수로 1회 구축 → 값 동일, 속도만 향상.
_SRGB2LIN = None
_LIN2SRGB = None


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


def solve_baseline_gain(target_mean, cam, ref, as_shot, lin_native):
    """카메라 네이티브 *선형광* 에 곱할 베이스라인 게인을 이미지별로 solve.

    화면 표시 평균 휘도가 임베드 JPEG 평균(target_mean)과 같아지는 게인을 찾는다
    (= 카메라 JPEG 밝기에 매칭). display mean 은 게인에 대해 단조증가 → 로그공간 이분법.
    target 없으면 고정 폴백(BASELINE_GAIN). ⚠️프리뷰(raw_loader)·export(pipeline) 동일 사용.
    고정배수와 달리 어두운/밝은 씬 모두 자기 JPEG 에 맞음(상대노출은 카메라 측광이 이미 반영)."""
    if not target_mean or not math.isfinite(target_mean) or target_mean <= 0:
        return BASELINE_GAIN
    M = cam_to_srgb_matrix(cam).astype(np.float32)
    rel = rel_gain(cam, ref, as_shot, 0.0).astype(np.float32)
    s = lin_native[::8, ::8].reshape(-1, 3)          # 빠른 통계용 서브샘플
    def disp_mean(g):
        d = linear_to_srgb(np.clip((s * (g * rel)) @ M.T, 0.0, 1.0))
        return float((d @ LUMA).mean())
    lo, hi = 0.25, 32.0
    for _ in range(24):
        g = math.sqrt(lo * hi)
        if disp_mean(g) < target_mean:
            lo = g
        else:
            hi = g
    return math.sqrt(lo * hi)


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
        as_shot, as_shot_tint = estimate_wb(cam_xyz, ref, raw.camera_whitebalance)
        target_mean = _embedded_jpeg_mean(raw)   # 이미지별 자동 노출 목표(카메라 JPEG 밝기)

        rgb16 = raw.postprocess(
            user_wb=baked_wb(cam_xyz, ref),     # TREF daylight 베이크(고정)
            output_color=rawpy.ColorSpace.raw,  # 카메라 네이티브(매트릭스 미적용)
            # ⚠️X-Trans 센서는 half_size(베이어 2×2 비닝 가정)에서 격자/색노이즈 아티팩트가
            #   생긴다. full 디코딩 + 빠른 LINEAR 디모자이크(~1s) 후 max_edge 로 축소.
            #   (export(pipeline)는 기본 디모자이크로 풀해상도 현상 — 동일하게 격자 없음.)
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR,
            output_bps=16,                      # 게인/감마를 numpy 로 적용(8bit 양자화 전)
            no_auto_bright=True,                # 자동 밝기 보정 OFF(상대노출 보존)
            gamma=(2.4, 12.92),                 # 감마 인코딩
            highlight_mode=rawpy.HighlightMode.Clip,
        )

    # full 디코딩(X-Trans 격자 회피)이라 무거운 numpy(베이스라인/렌즈/감마) 전에 먼저
    # max_edge 로 축소 → 축소본에서 처리(반응성 유지). 정수 2× 박스평균(빠른 AA) 반복 후
    # 남은 분수배만 가벼운 bilinear zoom(가우시안 full-res AA 보다 ~5배 빠름).
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

    # 베이스라인 노출: 카메라 네이티브 선형광에 이미지별 자동 게인 곱 → JPEG 수준 밝기.
    # 8bit 양자화 전 선형에서 적용해 섀도우 밴딩 방지. pow 대신 LUT 게더(rgb16=uint16).
    lin = _srgb2lin_lut()[rgb16]                         # (H,W,3) float32 선형
    lin *= solve_baseline_gain(target_mean, cam_xyz, ref, as_shot, lin)
    idx = (np.clip(lin, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    rgb = (_lin2srgb_lut()[idx] * 255.0 + 0.5).astype(np.uint8)

    if lens_correct:
        rgb = lens.apply(rgb)          # X100V 렌즈 프로파일(왜곡/주변광량/CA), 색공간 무관
    rgb = np.ascontiguousarray(rgb)
    h, w, _ = rgb.shape
    img = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    cam2srgb = cam_to_srgb_matrix(cam_xyz)
    return (img, int(as_shot), float(as_shot_tint), cam_xyz.flatten().tolist(),
            ref.tolist(), cam2srgb.flatten().tolist())

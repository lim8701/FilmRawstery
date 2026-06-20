"""풀해상도 export 파이프라인 (numpy).

화면 프리뷰(GPU 셰이더, 프록시)와 동일한 단계/수식을 풀해상도에 재현한다:

  WB(카메라네이티브 선형화→상대게인→cam->sRGB 매트릭스→sRGB) -> 노출 -> 톤영역
       -> 텍스처/클래리티/디헤이즈 -> 3D LUT -> 대비 -> 톤커브 -> 그레인 -> 비네팅

텍스처/클래리티는 공간(이웃) 연산이라 셰이더의 '프록시 텍셀' 반경을 풀해상도
비율(full/proxy)로 스케일해 시각적으로 맞춘다. 공간 단계는 전체 배열에서,
메모리 큰 3D LUT 단계는 가로 스트립으로 처리한다.
"""

import numpy as np
import rawpy
from PySide6.QtGui import QImage
from scipy.ndimage import gaussian_filter, zoom

import date_stamp
import lens
import wb
from wb import baked_wb, cam_to_srgb_matrix

LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _tone_zones(c, hi, sh, wh, bl):
    l = c @ LUMA
    sh_m = 1.0 - _smoothstep(0.0, 0.5, l)
    hi_m = _smoothstep(0.5, 1.0, l)
    bl_m = 1.0 - _smoothstep(0.0, 0.25, l)
    wh_m = _smoothstep(0.75, 1.0, l)
    delta = sh * 0.3 * sh_m + hi * 0.3 * hi_m + bl * 0.3 * bl_m + wh * 0.3 * wh_m
    return c + delta[..., None]


def _blur_rgb(c, sigma):
    return gaussian_filter(c, sigma=(sigma, sigma, 0), mode="nearest")


def _blur_luma(lum, sigma):
    return gaussian_filter(lum, sigma=sigma, mode="nearest")


def _texture(c, amt, sigma):
    # 중주파 디테일 = 원본 - 작은반경 가우시안 (셰이더와 동일 강도 1.6)
    return c + (c - _blur_rgb(c, sigma)) * amt * 1.6


def _clarity(c, amt, sigma):
    lum = c @ LUMA
    d = lum - _blur_luma(lum, sigma)
    mid = 1.0 - np.abs(2.0 * lum - 1.0)
    return c + (d * amt * 0.8 * mid)[..., None]


def _dehaze(c, amt, sigma):
    """톤 모델 디헤이즈 (프리뷰 셰이더와 동일). +대비/채도/로컬대비, -흰베일·플랫."""
    lum = c @ LUMA
    ld = lum - _blur_luma(lum, sigma)
    c = c + (ld * amt * 0.4)[..., None]               # 로컬 대비
    c = (c - 0.5) * (1.0 + amt * 0.25) + 0.5          # 대비
    if amt < 0:
        c = c + (0.92 - c) * ((-amt) * 0.22)          # 흰 베일(밝아짐)
    l = c @ LUMA
    return l[..., None] + (c - l[..., None]) * (1.0 + amt * 0.3)  # 채도


def _presence(c, sat, vib):
    """바이브런스/채도 (셰이더와 동일, luma 축 mix -> 휘도 보존)."""
    if vib != 0.0:
        lum = c @ LUMA
        cur = c.max(axis=2) - c.min(axis=2)
        f = 1.0 + vib * (1.0 - np.clip(cur, 0.0, 1.0))
        c = np.clip(lum[..., None] + (c - lum[..., None]) * f[..., None], 0.0, 1.0)
    if sat != 0.0:
        lum = c @ LUMA
        c = np.clip(lum[..., None] + (c - lum[..., None]) * (1.0 + sat), 0.0, 1.0)
    return c


def _apply_lut3d(c, lut, n):
    x = np.clip(c, 0.0, 1.0) * (n - 1)
    b0 = np.floor(x).astype(np.intp)
    b1 = np.minimum(b0 + 1, n - 1)
    f = x - b0
    r0, g0, bb0 = b0[..., 0], b0[..., 1], b0[..., 2]
    r1, g1, bb1 = b1[..., 0], b1[..., 1], b1[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c00 = lut[r0, g0, bb0] * (1 - fr) + lut[r1, g0, bb0] * fr
    c01 = lut[r0, g0, bb1] * (1 - fr) + lut[r1, g0, bb1] * fr
    c10 = lut[r0, g1, bb0] * (1 - fr) + lut[r1, g1, bb0] * fr
    c11 = lut[r0, g1, bb1] * (1 - fr) + lut[r1, g1, bb1] * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    return c0 * (1 - fb) + c1 * fb


def _downscale_to_edge(rgb16, out_edge):
    """rgb16 (uint16) 을 긴 변 = out_edge 로 비율 유지 다운스케일(안티에일리어싱).
    out_edge<=0 이거나 이미 작으면 원본 반환."""
    h, w = rgb16.shape[:2]
    m = max(h, w)
    if out_edge <= 0 or m <= out_edge:
        return rgb16
    f = out_edge / float(m)
    x = rgb16.astype(np.float32)
    sigma = 0.5 * (1.0 / f - 1.0)                 # 축소비에 맞춘 안티에일리어싱
    if sigma > 0.4:
        x = gaussian_filter(x, (sigma, sigma, 0.0))
    nh, nw = max(1, int(round(h * f))), max(1, int(round(w * f)))
    x = zoom(x, (nh / h, nw / w, 1.0), order=1)
    return np.clip(x + 0.5, 0.0, 65535.0).astype(np.uint16)


def render_full(path, kelvin, tint, p, lut_arr, lut_n, curve_lut,
                proxy_edge=2560, strip=256):
    """풀해상도 RAF 를 조정값으로 현상해 (H,W,3) uint8 RGB 로 반환."""
    with rawpy.imread(path) as raw:
        cam = np.array(raw.rgb_xyz_matrix)[:3, :3]
        ref = np.array(raw.daylight_whitebalance)[:3]
        ref = ref / ref[1]
        # 프록시와 동일: 카메라 네이티브(매트릭스 미적용) + TREF daylight 베이크 + 감마 저장.
        rgb16 = raw.postprocess(user_wb=baked_wb(cam, ref),
                                output_color=rawpy.ColorSpace.raw,
                                output_bps=16, no_auto_bright=True,
                                gamma=(2.4, 12.92),
                                highlight_mode=rawpy.HighlightMode.Clip)

    # 출력 해상도 지정(긴 변): 처리 전 다운스케일 -> 빠르고, 효과 sigma 가 해상도에
    # 비례해 룩 동일 유지(그레인/스탬프도 이미지 상대 크기라 일관).
    rgb16 = _downscale_to_edge(rgb16, int(p.get("outEdge", 0) or 0))
    if p.get("lensCorrection", True):
        rgb16 = lens.apply(rgb16)      # X100V 렌즈 프로파일(프록시와 동일, 색공간 무관)

    h, w, _ = rgb16.shape
    scale = max(h, w) / float(proxy_edge)     # 프록시 텍셀 반경 -> 풀해상도 px

    # WB 프론트엔드(셰이더 adjust.frag 와 동일 수학):
    # 카메라 네이티브 감마 -> 선형화 -> WB 상대게인(카메라공간) -> cam->sRGB 매트릭스 -> sRGB.
    nat = wb.srgb_to_linear(rgb16.astype(np.float32) / 65535.0)
    nat *= wb.rel_gain(cam, ref, kelvin, tint).astype(np.float32)
    M = cam_to_srgb_matrix(cam).astype(np.float32)
    nat = nat @ M.T
    disp = wb.linear_to_srgb(nat).astype(np.float32)   # display sRGB (0..1)

    exp = 2.0 ** float(p.get("exposure", 0.0))
    hi, sh = float(p.get("highlights", 0)), float(p.get("shadows", 0))
    wh, bl = float(p.get("whites", 0)), float(p.get("blacks", 0))
    tex = float(p.get("texAmt", p.get("texture", 0)))
    cla = float(p.get("clarity", 0))
    deh = float(p.get("dehaze", 0))
    vig = float(p.get("vignette", 0))
    con = float(p.get("contrast", 1.0))
    sat = float(p.get("saturation", 0))
    vib = float(p.get("vibrance", 0))
    lut_strength = float(p.get("lutStrength", 1.0))
    grain_amt = float(p.get("grainAmt", 0))
    grain_size = float(p.get("grainSize", 0.5))
    stamp_text = str(p.get("stampText", "") or "")
    do_stamp = bool(p.get("dateStamp", False)) and stamp_text != ""

    # --- 전역/공간 단계 (전체 배열) ---
    c = np.clip(disp * exp, 0.0, 1.0)
    c = np.clip(_tone_zones(c, hi, sh, wh, bl), 0.0, 1.0)
    sigma_tex = 1.5 * scale     # 프리뷰 텍스처 블러에 대응
    sigma_cla = 7.0 * scale     # 프리뷰 클래리티/디헤이즈 블러에 대응
    if tex != 0.0:
        c = _texture(c, tex, sigma_tex)
    if cla != 0.0:
        c = _clarity(c, cla, sigma_cla)
    if deh != 0.0:
        c = _dehaze(c, deh, sigma_cla)
    np.clip(c, 0.0, 1.0, out=c)

    # 비네팅 마스크(정규화 좌표, 해상도 무관)
    if vig != 0.0:
        yy = (np.arange(h, dtype=np.float32) / (h - 1)) - 0.5
        xx = (np.arange(w, dtype=np.float32) / (w - 1)) - 0.5
        rr = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2) / 0.7071
        vig_mask = (1.0 + vig * 0.8 * _smoothstep(0.35, 1.0, rr)).astype(np.float32)
    else:
        vig_mask = None

    # 필름 그레인 필드(흑백 단색, 전체 H,W 1회 생성 -> 스트립 시드 이음매 방지).
    # 셰이더 value-noise 와 '성격(셀 크기/강도)' 일치: gridN=mix(1500,150,size),
    # 정사각 입자(gridN/aspect)로 거친 그리드를 bilinear 업샘플. 패턴 픽셀일치는 기대 안 함.
    if grain_amt > 0.0:
        gridN = int(round(1500.0 + (500.0 - 1500.0) * grain_size))  # 셰이더 mix 와 동일
        gx, gy = gridN, max(1, int(round(gridN * h / w)))           # gridN / aspect(W/H)
        rng = np.random.default_rng(12345)                          # 재export 결정적
        grid = (rng.random((gy + 1, gx + 1), dtype=np.float32) - 0.5)
        grain2d = zoom(grid, (h / (gy + 1), w / (gx + 1)), order=1)
        grain2d = grain2d[:h, :w].astype(np.float32)
    else:
        grain2d = None

    # --- LUT/대비/커브/비네팅 (메모리 큰 LUT 는 스트립) ---
    out = np.empty((h, w, 3), dtype=np.uint8)
    xs = np.linspace(0.0, 1.0, len(curve_lut))
    cl = np.asarray(curve_lut, dtype=np.float32)
    for y in range(0, h, strip):
        blk = c[y:y + strip]
        if lut_arr is not None:
            looked = _apply_lut3d(blk, lut_arr, lut_n)
            blk = blk * (1.0 - lut_strength) + looked * lut_strength   # 강도 블렌딩
        if sat != 0.0 or vib != 0.0:
            blk = _presence(blk, sat, vib)                             # 바이브런스/채도
        blk = np.clip((blk - 0.5) * con + 0.5, 0.0, 1.0)
        for ch in range(3):
            blk[..., ch] = np.interp(blk[..., ch], xs, cl)
        if vig_mask is not None:
            blk = blk * vig_mask[y:y + strip, :, None]
        out[y:y + strip] = np.rint(np.clip(blk, 0.0, 1.0) * 255.0).astype(np.uint8)

    # 날짜 스탬프(필름 데이트백) — 비네팅 뒤 우하단 코너 가산(렌즈 비네팅 영향 없음)
    if do_stamp:
        date_stamp.stamp_export(out, stamp_text)   # uint8 코너만 in-place

    # 필름 그레인 — 맨 끝: 장면과 스탬프 모두에 입혀짐(에멀전 입자, 셰이더와 동일 순서)
    if grain2d is not None:
        f = out.astype(np.float32) / 255.0
        f += grain2d[..., None] * grain_amt * 0.12
        out = np.rint(np.clip(f, 0.0, 1.0) * 255.0).astype(np.uint8)

    return out


def save_image(arr_u8, path) -> bool:
    """(H,W,3) uint8 -> 파일 저장 (확장자로 포맷 추론: jpg/png/tif 8bit)."""
    arr_u8 = np.ascontiguousarray(arr_u8)
    h, w, _ = arr_u8.shape
    img = QImage(arr_u8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    return bool(img.save(path))

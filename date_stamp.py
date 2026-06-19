"""필름 'Date Stamp'(쿼츠 데이트백) 오버레이 렌더.

EXIF 촬영일시를 7-세그먼트(DSEG7) 호박색 숫자로 그리고, 가우시안 글로우(블룸)를
입혀 이미지 우하단에 합성한다. 프리뷰(프록시)와 export(풀해상도)가 같은 함수를
같은 '이미지 상대' 비율(TEXT_FRAC/MARGIN_FRAC)로 호출 → 동일한 룩.

폰트: fonts/DSEG7Classic-Bold.ttf (SIL OFL, keshikan/DSEG). 아포스트로피('),
슬래시(/)는 DSEG7 에 없어 Qt 폴백 폰트로 렌더되지만 글로우에 묻혀 무방.
"""
import os

import numpy as np
from scipy.ndimage import gaussian_filter, grey_dilation, zoom
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QImage,
                           QPainter)

_FONT_PATH = os.path.join(os.path.dirname(__file__), "fonts", "DSEG7Classic-Bold.ttf")
_family = None

# --- 이미지 상대 기하/룩 (프리뷰·export 단일 소스) ---
# 기준은 '짧은 변' -> 가로/세로 방향 무관하게 같은 상대 크기.
TEXT_FRAC = 0.040       # 숫자 높이 = 짧은 변의 4.0%
MARGIN_FRAC = 0.030     # 우/하 여백 = 짧은 변의 3.0%
# 필름 광학 각인의 색: 핫코어(밝은 주황-노랑) → 앰버 → 적주황 헤일로로 번짐.
C_CORE = np.array([1.00, 0.95, 0.76], np.float32)   # 노출 과다된 뜨거운 중심(흰빛쪽, 더 밝게)
C_MID = np.array([1.00, 0.54, 0.16], np.float32)    # 앰버
C_HALO = np.array([0.94, 0.24, 0.06], np.float32)   # 적주황 외곽 번짐
# source-over(알파) 합성의 불투명도 배율. 배경 밝기와 무관하게 일정한 룩.
STAMP_STRENGTH = 0.92   # 셰이더 ubuf.stampStrength = QML pipe.stampStrength 와 일치
STAMP_CORE_OPACITY = 0.70  # 코어(숫자) 최대 불투명도 (<1 이면 배경이 비침). 셰이더 리터럴과 일치
STAMP_GLOW_GAIN = 1.2      # screen 글로우(빛 가산) 게인 — 클수록 더 밝게 탐. 셰이더 리터럴과 일치


def font_family():
    """번들 DSEG7 폰트를 1회 등록하고 패밀리명을 반환(실패 시 monospace)."""
    global _family
    if _family is None:
        fid = QFontDatabase.addApplicationFont(_FONT_PATH)
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        _family = fams[0] if fams else "monospace"
    return _family


def _alpha_from_qimage(img):
    """ARGB32 QImage → (H,W) float alpha [0,1]. (ARGB32 는 bytesPerLine=4w, 패딩 없음)"""
    img = img.convertToFormat(QImage.Format.Format_ARGB32)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = (np.frombuffer(ptr, np.uint8)
           .reshape(h, img.bytesPerLine())[:, :w * 4]
           .reshape(h, w, 4))
    return arr[..., 3].astype(np.float32) / 255.0   # ARGB32(LE)=B,G,R,A


def render_sprite(text, text_h_px):
    """필름 광학 각인 스타일 날짜 스프라이트를 RGBA float (H,W,4) [0,1] 로 반환.
    코어(살짝 번짐)→다층 헤일로, 핫코어→앰버→적주황 색 그라데이션, 불규칙 번짐."""
    text_h_px = max(6.0, float(text_h_px))
    fam = font_family()
    f = QFont(fam)
    f.setPixelSize(int(round(text_h_px)))
    fm = QFontMetrics(f)
    tw = fm.horizontalAdvance(text)
    th = fm.height()
    pad = int(round(text_h_px * 1.6))      # 넓은 글로우 여유(번짐 크게 확장)
    W, H = tw + 2 * pad, th + 2 * pad

    canvas = QImage(W, H, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(0, 0, 0, 0))
    p = QPainter(canvas)
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 255))   # 흰색으로 그려 알파만 사용
    p.drawText(pad, pad + fm.ascent(), text)
    p.end()
    m = _alpha_from_qimage(canvas)         # 숫자 알파 마스크

    # 빛이 에멀전에 스며든 듯: 코어도 살짝 번지게 + 다중 반경 헤일로(멀리 퍼지는 번짐)
    core = gaussian_filter(m, text_h_px * 0.022)
    gnear = gaussian_filter(m, text_h_px * 0.080)
    gfar = gaussian_filter(m, text_h_px * 0.300)
    # 사이드까지 균일한 글로우: 글자열을 가로로 팽창해 연속 띠로 만든 뒤 블러
    # -> 단일 가우시안(중앙만 밝음)보다 끝단까지 고르게 채워짐.
    dil = grey_dilation(m, size=(max(1, int(text_h_px * 0.22)),
                                 max(1, int(text_h_px * 0.95))))
    gband = gaussian_filter(dil, text_h_px * 0.42)

    # 헤일로는 피크 정규화 -> 넓게 퍼져도 밝기를 유지(가우시안 진폭 급감 보정 = 번짐 가시화)
    def _nrm(x):
        return x / (float(x.max()) + 1e-6)
    w_core = core * 1.0
    w_mid = _nrm(gnear) * 0.50
    w_halo = _nrm(gfar) * 0.30
    w_band = _nrm(gband) * 0.42   # 균일한 사이드 글로우(가로 팽창 띠)
    wsum = w_core + w_mid + w_halo + w_band + 1e-6
    rgb = (w_core[..., None] * C_CORE
           + w_mid[..., None] * C_MID
           + (w_halo + w_band)[..., None] * C_HALO) / wsum[..., None]   # 코어=핫, 외곽=적주황

    # 불규칙한 번짐(유기적): 저주파 노이즈로 헤일로 강도만 변조(코어는 또렷이 유지)
    rng = np.random.default_rng(7)
    gh, gw = max(2, H // 36), max(2, W // 36)
    nlow = zoom(rng.random((gh, gw), dtype=np.float32),
                (H / gh, W / gw), order=1)[:H, :W]
    glow = (w_mid + w_halo + w_band) * (0.78 + 0.22 * nlow)
    inten = np.clip(w_core + glow, 0.0, 1.0)

    rgba = np.empty((H, W, 4), np.float32)
    rgba[..., :3] = np.clip(rgb, 0.0, 1.0)
    rgba[..., 3] = inten
    return rgba


def _placement(sprite, img_w, img_h, margin_px):
    """sprite 를 우하단에 둘 때의 (x0,y0,sprite_clipped)."""
    sh, sw, _ = sprite.shape
    x0 = max(0, img_w - margin_px - sw)
    y0 = max(0, img_h - margin_px - sh)
    sp = sprite[:img_h - y0, :img_w - x0]
    return x0, y0, sp


def stamp_export(out_u8, text):
    """export(풀해상도) 합성: out_u8 (H,W,3) uint8 의 '우하단 코너에만' 하이브리드 합성.
    코어=source-over(배경무관 일관), 헤일로=screen 가산(빛 번짐) — 셰이더와 동일."""
    H, W, _ = out_u8.shape
    short = min(H, W)
    sprite = render_sprite(text, TEXT_FRAC * short)
    x0, y0, sp = _placement(sprite, W, H, int(round(MARGIN_FRAC * short)))
    sh, sw, _ = sp.shape
    col = sp[..., :3]
    a = np.clip(sp[..., 3] * STAMP_STRENGTH, 0.0, 1.0)       # (h,w)
    t = np.clip((a - 0.45) / (0.85 - 0.45), 0.0, 1.0)
    coreA = (t * t * (3.0 - 2.0 * t))[..., None] * STAMP_CORE_OPACITY   # 코어 불투명도(배경 비침)
    region = out_u8[y0:y0 + sh, x0:x0 + sw, :].astype(np.float32) / 255.0
    region = region * (1.0 - coreA) + col * coreA            # 코어 source-over
    glow = col * np.clip(a[..., None] * (1.0 - coreA * 0.5) * STAMP_GLOW_GAIN, 0.0, 1.0)
    region = 1.0 - (1.0 - region) * (1.0 - glow)             # screen 가산(코어도 일부 태움)
    out_u8[y0:y0 + sh, x0:x0 + sw, :] = np.rint(np.clip(region, 0.0, 1.0) * 255.0).astype(np.uint8)
    return out_u8


def preview_layer_qimage(text, img_w, img_h):
    """프리뷰용: (img_w,img_h) 투명 RGBA QImage 에 우하단 스탬프 합성 → QImage(ARGB32).
    pipeView 에 그대로 stretch 하면 export 와 동일 위치/상대크기로 정합."""
    short = min(img_w, img_h)
    sprite = render_sprite(text, TEXT_FRAC * short)
    margin = int(round(MARGIN_FRAC * short))
    x0, y0, sp = _placement(sprite, img_w, img_h, margin)
    sh, sw, _ = sp.shape

    u8 = np.zeros((img_h, img_w, 4), np.uint8)        # ARGB32(LE)=B,G,R,A
    u8[y0:y0 + sh, x0:x0 + sw, 0] = np.clip(sp[..., 2], 0, 1) * 255  # B
    u8[y0:y0 + sh, x0:x0 + sw, 1] = np.clip(sp[..., 1], 0, 1) * 255  # G
    u8[y0:y0 + sh, x0:x0 + sw, 2] = np.clip(sp[..., 0], 0, 1) * 255  # R
    u8[y0:y0 + sh, x0:x0 + sw, 3] = np.clip(sp[..., 3], 0, 1) * 255  # A
    u8 = np.ascontiguousarray(u8)
    return QImage(u8.data, img_w, img_h, 4 * img_w, QImage.Format.Format_ARGB32).copy()


def stamp_text_from_date(date_str):
    """exif_info 의 'YYYY-MM-DD HH:MM:SS' → 클래식 "'YY MM DD" (예: '24 05 12).
    파싱 실패 시 빈 문자열."""
    if not date_str:
        return ""
    try:
        d = date_str.split()[0]                # YYYY-MM-DD
        y, m, day = d.split("-")[:3]
        return f"'{y[-2:]} {int(m):02d} {int(day):02d}"
    except Exception:
        return ""

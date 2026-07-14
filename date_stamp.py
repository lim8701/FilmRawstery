"""필름 'Date Stamp'(쿼츠 데이트백) 오버레이 렌더.

EXIF 촬영일시를 7-세그먼트(DSEG7) 호박색 숫자로 그리고, 가우시안 글로우(블룸)를
입혀 이미지 우하단에 합성한다. 프리뷰(프록시)와 export(풀해상도)가 같은 함수를
같은 '이미지 상대' 비율(TEXT_FRAC/MARGIN_FRAC)로 호출 → 동일한 룩.

폰트: fonts/DSEG7Classic-Bold.ttf (SIL OFL, keshikan/DSEG). 아포스트로피('),
슬래시(/)는 DSEG7 에 없어 Qt 폴백 폰트로 렌더되지만 글로우에 묻혀 무방.
"""
import os
import sys
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter, grey_dilation, zoom
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetrics, QImage,
                           QPainter)


def _asset_base() -> Path:
    """폰트 등 번들 자산 위치. frozen(PyInstaller/Nuitka) 인식. (main.app_base 와 동일 로직,
    순환 임포트 방지를 위해 모듈 내부에 둠.)"""
    if getattr(sys, "frozen", False):
        mp = getattr(sys, "_MEIPASS", None)
        return Path(mp) if mp else Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_FONT_PATH = str(_asset_base() / "fonts" / "DSEG7Classic-Bold.ttf")
_family = None

# --- 이미지 상대 기하/룩 (프리뷰·export 단일 소스) ---
# 기준은 '짧은 변' -> 가로/세로 방향 무관하게 같은 상대 크기.
TEXT_FRAC = 0.032       # 숫자 높이 = 짧은 변의 3.2% (실제 필름 데이트백에 가까운 크기)
MARGIN_FRAC = 0.030     # 우/하 여백 = 짧은 변의 3.0%
# 필름 광학 각인의 색: 핫코어(밝은 주황-노랑) → 앰버 → 적주황 헤일로로 번짐.
C_CORE = np.array([1.00, 0.95, 0.76], np.float32)   # 노출 과다된 뜨거운 중심(흰빛쪽, 더 밝게)
C_MID = np.array([1.00, 0.54, 0.16], np.float32)    # 앰버
C_HALO = np.array([0.94, 0.24, 0.06], np.float32)   # 적주황 외곽 번짐
# source-over(알파) 합성의 불투명도 배율. 배경 밝기와 무관하게 일정한 룩.
# 스탬프는 크롭/회전이 끝난 '최종 프레임'에 source-over 로 찍는다(export=numpy, 프리뷰=QML
# Image 오버레이 동일 합성). 스프라이트 RGBA 에 핫코어→앰버→헤일로 글로우가 이미 베이크돼 있어
# 단순 source-over 로도 빛나는 데이트백 룩이 난다.
STAMP_STRENGTH = 0.92   # 프리뷰 stampOverlay.opacity 와 일치


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
    if nlow.shape != (H, W):     # zoom 라운딩이 언더슈트하면 슬라이스로 못 채움 → edge 패드
        nlow = np.pad(nlow, ((0, H - nlow.shape[0]), (0, W - nlow.shape[1])), mode="edge")
    glow = (w_mid + w_halo + w_band) * (0.78 + 0.22 * nlow)
    inten = np.clip(w_core + glow, 0.0, 1.0)
    col = np.clip(rgb, 0.0, 1.0)

    # 단순 source-over(프리뷰 QML Image + export numpy 동일 합성)만으로도 예전 '하이브리드'(코어
    # source-over + screen 글로우) 룩이 나도록, '검은 배경 위 하이브리드 결과'를 스프라이트에 미리
    # 베이크한다. 알파 = 그 결과의 밝기(피크 채널) → 어두운 배경에선 예전과 동일, 밝은 배경에선
    # screen 처럼 빛을 더한다. STAMP_STRENGTH 는 합성 때 알파에 곱해지므로 여기서 함께 반영.
    s = STAMP_STRENGTH
    aa = np.clip(inten * s, 0.0, 1.0)[..., None]
    t = np.clip((aa - 0.45) / 0.40, 0.0, 1.0)
    coreA = (t * t * (3.0 - 2.0 * t)) * 0.70                       # smoothstep(0.45,0.85,aa)*0.70
    core_black = col * coreA                                       # 코어 over black
    g = col * np.clip(aa * (1.0 - coreA * 0.5) * 1.2, 0.0, 1.0)    # screen 글로우 항
    ob = 1.0 - (1.0 - core_black) * (1.0 - g)                      # 예전 하이브리드 over black
    A2 = np.clip(ob.max(axis=2, keepdims=True), 0.0, 1.0)         # 알파 = 밝기(피크 채널)
    col2 = ob / np.maximum(A2, 1e-4)                              # 색(피크 정규화 → 핫 휴 유지)

    rgba = np.empty((H, W, 4), np.float32)
    rgba[..., :3] = np.clip(col2, 0.0, 1.0)
    rgba[..., 3] = np.clip(A2[..., 0] / s, 0.0, 1.0)             # 합성 때 ×s → 실효 알파 = A2
    return rgba


# --- 촬영 방향(데이트백 현실 반영) ---
# 실제 쿼츠 데이트백은 '센서(가로) 프레임의 우하단'에 각인된다. 세로로 촬영하면(센서를 회전)
# 업라이트로 볼 때 각인이 90° 돌아간 채 대응 코너로 이동한다. EXIF Orientation 으로 센서→업라이트
# 회전(CW)을 구해 스프라이트를 같은 각도로 돌리고 대응 코너에 배치한다(프리뷰=export 동일).
_ROT_FROM_ORI = {1: 0, 3: 180, 6: 90, 8: 270}      # EXIF Orientation -> 업라이트로 만든 CW 회전(도)
_ROT_CORNER = {0: "br", 90: "bl", 180: "tl", 270: "tr"}  # 그 회전 후 센서 우하단이 오는 코너


def rot_from_orientation(ori) -> int:
    """EXIF Orientation(1~8) -> 센서(가로)를 업라이트로 만든 CW 회전(0/90/180/270). 미러는 0 폴백."""
    try:
        return _ROT_FROM_ORI.get(int(ori), 0)
    except Exception:
        return 0


def corner_for_rot(rot) -> str:
    """CW 회전(도) -> 업라이트 프레임에서 데이트백이 오는 코너('br'/'bl'/'tl'/'tr')."""
    return _ROT_CORNER.get(int(rot) % 360, "br")


def _rotate_sprite(sprite, rot):
    """스프라이트(가로 텍스트)를 CW 회전(도)만큼 회전. np.rot90 은 CCW 라 k=-회전/90."""
    k = (int(rot) // 90) % 4
    return sprite if k == 0 else np.ascontiguousarray(np.rot90(sprite, k=-k))


def _placement(sprite, img_w, img_h, margin_px, corner="br"):
    """sprite 를 지정 코너에 둘 때의 (x0,y0,sprite_clipped). corner='br'/'bl'/'tl'/'tr'."""
    sh, sw, _ = sprite.shape
    right = corner in ("br", "tr")
    bottom = corner in ("br", "bl")
    x0 = max(0, img_w - margin_px - sw) if right else min(margin_px, max(0, img_w - sw))
    y0 = max(0, img_h - margin_px - sh) if bottom else min(margin_px, max(0, img_h - sh))
    sp = sprite[:img_h - y0, :img_w - x0]
    return x0, y0, sp


def stamp_export(out, text, rot=0):
    """크롭/회전까지 끝난 '최종 프레임' out (H,W,3) 의 코너에 날짜 스프라이트를 source-over
    합성(in-place). rot=촬영 방향(센서→업라이트 CW 회전) — 데이트백을 센서 우하단 각인처럼
    회전·코너 배치(세로 사진은 90° 돌아간 코너). 위치/크기는 out 짧은 변 기준(크롭 후에도 일정).
    프리뷰 QML Image 오버레이와 동일 합성·회전(프리뷰=export). dtype 으로 비트깊이 자동 인식."""
    mx = 65535.0 if out.dtype == np.uint16 else 255.0
    H, W, _ = out.shape
    short = min(H, W)
    sprite = _rotate_sprite(render_sprite(text, TEXT_FRAC * short), rot)
    x0, y0, sp = _placement(sprite, W, H, int(round(MARGIN_FRAC * short)), corner_for_rot(rot))
    sh, sw, _ = sp.shape
    col = sp[..., :3]
    a = np.clip(sp[..., 3:4] * STAMP_STRENGTH, 0.0, 1.0)     # (h,w,1)
    region = out[y0:y0 + sh, x0:x0 + sw, :].astype(np.float32) / mx
    region = region * (1.0 - a) + col * a                    # source-over
    out[y0:y0 + sh, x0:x0 + sw, :] = np.rint(np.clip(region, 0.0, 1.0) * mx).astype(out.dtype)
    return out


def sprite_layer(text, ref_short=1000.0, rot=0):
    """프리뷰 오버레이용 '타이트' 날짜 스프라이트(글로우 패딩 포함) → (QImage, wRatio, hRatio).
    rot=촬영 방향(센서→업라이트 CW 회전)으로 스프라이트를 미리 회전(export 와 동일 픽셀).
    wRatio/hRatio = (회전 후) 스프라이트 (W,H) / 짧은 변. QML 이 cropClip 짧은 변에 이 비율을 곱해
    Image 크기를, controller.stampCorner 코너에 MARGIN_FRAC 마진으로 배치하면 export(stamp_export,
    동일 TEXT_FRAC/MARGIN_FRAC·회전·코너)와 같은 위치/상대크기·source-over 합성이 된다(프리뷰=export)."""
    sp = _rotate_sprite(render_sprite(text, TEXT_FRAC * ref_short), rot)   # (H,W,4) float
    sh, sw, _ = sp.shape
    u8 = np.empty((sh, sw, 4), np.uint8)              # ARGB32(LE)=B,G,R,A
    u8[..., 0] = np.clip(sp[..., 2], 0, 1) * 255      # B
    u8[..., 1] = np.clip(sp[..., 1], 0, 1) * 255      # G
    u8[..., 2] = np.clip(sp[..., 0], 0, 1) * 255      # R
    u8[..., 3] = np.clip(sp[..., 3], 0, 1) * 255      # A
    u8 = np.ascontiguousarray(u8)
    img = QImage(u8.data, sw, sh, 4 * sw, QImage.Format.Format_ARGB32).copy()
    return img, sw / float(ref_short), sh / float(ref_short)


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

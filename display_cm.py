"""디스플레이 색관리(프리뷰 전용): 현재 모니터의 ICC 프로파일을 읽어
sRGB→디스플레이 3D LUT 아틀라스를 만든다.

비색관리 표시 경로에서 sRGB 값이 광색역 패널(예: XPS OLED ~P3)에 과포화로
나오는 것을, sRGB→패널 변환을 **미리** 적용해 사전 보정한다(화면이 정확한 sRGB
=export 로 보이게). 모니터별 실제 프로파일을 쓰므로 색역·화이트포인트·TRC 가
정확하고, sRGB 모니터에선 변환이 항등이 돼 영향이 없다.

LUT 아틀라스 포맷/좌표 규약은 필름시뮬 LUT(lut.atlas_qimage)와 동일 →
셰이더의 트라이리니어 샘플링 코드를 그대로 재사용한다.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

import numpy as np
from PySide6.QtGui import QColorSpace, QImage


def display_icc_path(device_name: str | None = None) -> str | None:
    """주어진 디스플레이의 ICC 프로파일 경로(Windows). device_name 예: '\\\\.\\DISPLAY1'.
    실패/없음 시 None."""
    try:
        gdi32 = ctypes.windll.gdi32
        gdi32.CreateDCW.restype = wintypes.HDC
        gdi32.CreateDCW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                    wintypes.LPCWSTR, ctypes.c_void_p]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]   # HDC(큰 정수) 변환 — 미지정 시 OverflowError
        hdc = gdi32.CreateDCW("DISPLAY", device_name, None, None)
        if not hdc:
            return None
        try:
            gdi32.GetICMProfileW.restype = wintypes.BOOL
            gdi32.GetICMProfileW.argtypes = [wintypes.HDC,
                                             ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR]
            size = wintypes.DWORD(260)
            buf = ctypes.create_unicode_buffer(size.value)
            if gdi32.GetICMProfileW(hdc, ctypes.byref(size), buf):
                return buf.value
            # 버퍼 부족 시 size 에 필요한 길이가 채워짐 → 재시도
            buf = ctypes.create_unicode_buffer(size.value)
            if gdi32.GetICMProfileW(hdc, ctypes.byref(size), buf):
                return buf.value
            return None
        finally:
            gdi32.DeleteDC(hdc)
    except Exception:
        return None


def _srgb_space() -> QColorSpace:
    return QColorSpace(QColorSpace.NamedColorSpace.SRgb)


def build_cm_atlas(icc_path: str | None, n: int = 33):
    """sRGB→디스플레이(icc) 3D LUT 아틀라스(QImage, 폭 n*n, 높이 n) + n 반환.

    필름시뮬 아틀라스와 동일 레이아웃: 픽셀(x=b*n+r, y=g) = 변환된 색(r,g,b)/(n-1).
    프로파일이 없거나 sRGB 와 같으면 (None, 0) 반환(색관리 불필요=항등)."""
    dst = _load_dst_space(icc_path)
    if dst is None:
        return None, 0
    src = _srgb_space()
    xform = src.transformationToColorSpace(dst)

    # 아틀라스 레이아웃으로 입력 sRGB 그리드 직접 구성 → applyColorTransform 한 번에.
    idx = (np.arange(n, dtype=np.float64) / (n - 1))
    grid = np.zeros((n, n * n, 3), np.float64)
    r = idx[None, :]                      # x 안에서 r 이 가장 빠르게
    for b in range(n):
        grid[:, b * n:(b + 1) * n, 0] = r          # R
        grid[:, b * n:(b + 1) * n, 2] = idx[b]     # B (타일 선택)
    grid[:, :, 1] = idx[:, None]                   # G (행)
    a8 = np.ascontiguousarray((np.clip(grid, 0, 1) * 255.0 + 0.5).astype(np.uint8))
    h, w, _ = a8.shape
    img = QImage(a8.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
    img.applyColorTransform(xform)        # sRGB → 디스플레이(in-place)
    return img, n


def _load_dst_space(icc_path: str | None):
    """ICC 경로 → 유효한 디스플레이 QColorSpace. sRGB 동일/무효/없음이면 None(=항등)."""
    if not icc_path:
        return None
    try:
        with open(icc_path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    dst = QColorSpace.fromIccProfile(data)
    if not dst.isValid():
        return None
    if dst == _srgb_space():
        return None                       # 이미 sRGB 모니터 → 보정 불필요
    return dst

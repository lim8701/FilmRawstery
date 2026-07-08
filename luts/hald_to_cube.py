"""Hald CLUT PNG → .cube 변환기 (흑백 필름 시뮬레이션 추가용).

Stuart Sowerby 의 후지 필름 시뮬레이션 프로파일 등은 HaldCLUT PNG 로 배포되는데,
이 앱은 .cube 만 읽으므로 변환이 필요하다. Hald CLUT 은 3D LUT 을 이미지로 편 것
(픽셀 row-major 순서 = cube 의 R-fastest 엔트리 순서)이라 변환은 재배열이 전부다.

사용법 (프로젝트 venv 에서):
  python luts/hald_to_cube.py "Fuji XTrans III - Acros.png" luts/acros.cube

- 입력 크기 자동 감지(레벨 8=512², N=64 / 레벨 12=1728², N=144 등).
- N>64 는 기본으로 64 로 리샘플: 이 앱의 LUT 아틀라스 폭은 N²px 라(N=144 → 20736px)
  GPU 최대 텍스처 크기(통상 16384px)를 초과한다. --size 로 명시 지정 가능.
- PNG 읽기는 PySide6(QImage, 16bit PNG 지원) 우선, 없으면 Pillow 폴백(8bit).
"""

import argparse
import sys

import numpy as np


def read_png_rgb(path: str) -> np.ndarray:
    """PNG → (H,W,3) float64 [0,1]. PySide6(16bit 지원) 우선, Pillow 폴백."""
    try:
        from PySide6.QtGui import QImage
        img = QImage(path)
        if img.isNull():
            sys.exit(f"이미지를 열 수 없습니다: {path}")
        img = img.convertToFormat(QImage.Format.Format_RGBA64)
        w, h = img.width(), img.height()
        buf = (np.frombuffer(img.constBits(), np.uint16)
               .reshape(h, img.bytesPerLine() // 2)[:, :w * 4].reshape(h, w, 4))
        return buf[..., :3].astype(np.float64) / 65535.0
    except ImportError:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        return np.asarray(im, np.float64) / 255.0


def main() -> None:
    ap = argparse.ArgumentParser(description="Hald CLUT PNG -> .cube")
    ap.add_argument("input", help="Hald CLUT PNG 파일")
    ap.add_argument("output", help="출력 .cube 경로 (예: luts/acros.cube)")
    ap.add_argument("--size", type=int, default=0,
                    help="출력 큐브 한 변 N (기본: 원본 유지, 단 64 초과면 64 로 축소)")
    a = ap.parse_args()

    rgb = read_png_rgb(a.input)
    h, w, _ = rgb.shape
    n = round((w * h) ** (1.0 / 3.0))
    if n ** 3 != w * h:
        sys.exit(f"Hald CLUT 이미지가 아닙니다 ({w}x{h}: 픽셀 수가 N^3 이 아님)")

    # row-major 픽셀 순서 = R 이 가장 빠르게 변함 → reshape 축은 [B][G][R]
    lut = rgb.reshape(-1, 3).reshape(n, n, n, 3)

    out_n = a.size if a.size > 0 else (64 if n > 64 else n)
    if out_n != n:
        from scipy.ndimage import map_coordinates
        g = np.linspace(0.0, n - 1.0, out_n)
        bb, gg, rr = np.meshgrid(g, g, g, indexing="ij")
        lut = np.stack([map_coordinates(lut[..., c], [bb, gg, rr], order=1)
                        for c in range(3)], axis=-1)
        print(f"[hald_to_cube] {n}^3 -> {out_n}^3 리샘플 (앱 아틀라스 텍스처 한계 대응)")
        n = out_n

    flat = np.clip(lut, 0.0, 1.0).reshape(-1, 3)   # [B][G][R] 순회 == cube 표준 순서
    with open(a.output, "w", encoding="ascii") as f:
        f.write(f"# Converted from Hald CLUT by luts/hald_to_cube.py\n")
        f.write(f"LUT_3D_SIZE {n}\n")
        for r, gv, b in flat:
            f.write(f"{r:.6f} {gv:.6f} {b:.6f}\n")
    print(f"[hald_to_cube] OK -> {a.output}  (N={n})")


if __name__ == "__main__":
    main()

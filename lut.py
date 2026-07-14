"""표준 .cube 3D LUT 로더 + 셰이더용 2D 아틀라스 변환.

3D LUT 를 셰이더에서 쓰려면 sampler3D 가 필요한데, Qt Quick ShaderEffect 는
2D 텍스처(Image)만 property 로 받는다. 그래서 3D LUT 를 가로로 N 개 타일을
이어 붙인 2D 아틀라스(폭 N*N, 높이 N)로 펴서 넘기고, 셰이더에서 수동으로
트라이리니어 보간한다.

아틀라스 좌표 규약 (셰이더와 반드시 일치):
    blue = b 슬라이스를 b 번째 타일에 배치
    픽셀 (x = b*N + r,  y = g) 위치에 LUT[r, g, b] 값
"""

import numpy as np
from PySide6.QtGui import QImage


def load_cube(path: str):
    """Adobe .cube 파일을 (N, N, N, 3) float32 배열과 크기 N 으로 반환.

    데이터 순서는 red 가 가장 빠르게 변함: index = r + g*N + b*N*N
    """
    size = None
    dom_min, dom_max = None, None
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:  # BOM 있는 익스포터 대응(첫 키워드 보존)
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split()
            key = parts[0].upper()
            if key == "LUT_3D_SIZE":
                if len(parts) < 2:
                    raise ValueError(f"LUT_3D_SIZE 값 없음: {path}")
                size = int(parts[1])
            elif key == "DOMAIN_MIN":
                dom_min = [float(x) for x in s.split()[1:4]]
            elif key == "DOMAIN_MAX":
                dom_max = [float(x) for x in s.split()[1:4]]
            elif key in ("TITLE", "LUT_1D_SIZE"):
                continue
            else:
                parts = s.split()
                if len(parts) == 3:
                    try:
                        rows.append([float(parts[0]), float(parts[1]), float(parts[2])])
                    except ValueError:
                        continue
    if size is None:
        raise ValueError(f"LUT_3D_SIZE 없음: {path}")
    # 파이프라인/셰이더는 입력을 [0,1]로 가정하고 LUT 를 샘플한다. 비표준 도메인
    # (예: DOMAIN_MAX 4 4 4)은 조용히 잘못된 색을 내므로 최소한 경고한다(미지원).
    if (dom_min is not None and any(abs(v) > 1e-6 for v in dom_min)) or \
       (dom_max is not None and any(abs(v - 1.0) > 1e-6 for v in dom_max)):
        print(f"[lut] ⚠️비표준 DOMAIN(min={dom_min} max={dom_max}) — [0,1] 로 가정해 로드"
              f"(색이 어긋날 수 있음): {path}")

    data = np.asarray(rows, dtype=np.float32)
    if data.shape[0] != size ** 3:
        raise ValueError(
            f"데이터 개수 불일치: {data.shape[0]} != {size**3} ({path})"
        )

    idx = np.arange(size ** 3)
    r = idx % size
    g = (idx // size) % size
    b = idx // (size * size)
    lut = np.zeros((size, size, size, 3), dtype=np.float32)
    lut[r, g, b, :] = data
    return lut, size


def atlas_qimage(lut: np.ndarray, size: int) -> QImage:
    """(N,N,N,3) LUT 를 폭 N*N, 높이 N 의 RGB888 아틀라스 QImage 로 변환."""
    n = size
    atlas = np.zeros((n, n * n, 3), dtype=np.uint8)
    vals = np.clip(lut, 0.0, 1.0)
    vals = np.rint(vals * 255.0).astype(np.uint8)
    for b in range(n):
        # lut[r, g, b] -> atlas[y=g, x=b*n + r]  (r,g 축 transpose)
        tile = vals[:, :, b, :]                 # [r, g, 3]
        atlas[:, b * n:(b + 1) * n, :] = np.transpose(tile, (1, 0, 2))

    atlas = np.ascontiguousarray(atlas)
    h, w, _ = atlas.shape
    return QImage(atlas.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

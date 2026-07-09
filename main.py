"""RAW 에디터 최소 동작 스켈레톤.

  RAF 디코딩(rawpy) -> 프록시 QImage -> QML ShaderEffect(GPU) 파이프라인.
  프래그먼트 셰이더는 시작 시 번들 qsb 로 자동 컴파일한다(ensure_shader).

사용:
  pip install -r requirements.txt
  python main.py [선택: 열어둘 RAF 경로]
"""

import io
import json
import os
import shutil
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (Property, QBuffer, QFileSystemWatcher, QObject,
                            QSettings, QSize, Qt, QTimer, Signal, Slot, QUrl)
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QTransform
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider

# ⚠️ numpy/scipy/rawpy 등을 끌어오는 무거운 모듈(date_stamp, make_luts, exif_info, wb,
#    lut, raw_loader)은 여기서 임포트하지 않는다. 최상단에 두면 QGuiApplication/splash 가
#    뜨기 전에 전부 로드돼 '아무 동작 없는' 대기 구간이 길어진다. main() 에서 splash 를
#    띄운 *직후* _load_heavy_modules() 로 로드한다(체감 시작 시간 단축).

def app_base() -> Path:
    """번들 자산(qml/shaders/luts/fonts)이 위치한 디렉터리.

    - PyInstaller onedir: 자산이 sys._MEIPASS 아래로 해제됨
    - Nuitka standalone(pyside6-deploy): 자산이 exe 옆에 위치
    - dev(비-frozen): 소스 디렉터리(기존과 동일)
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)                          # PyInstaller
        return Path(sys.executable).resolve().parent      # Nuitka standalone
    return Path(__file__).resolve().parent


BASE = app_base()
SHADERS_DIR = BASE / "shaders"
SHADER_NAMES = ["adjust.frag", "blur.frag", "convert.frag", "displaycm.frag"]
LUTS_DIR = BASE / "luts"
APP_VERSION = "1.1.0"   # SemVer(MAJOR.MINOR.PATCH). 올릴 때 packaging/version_info.txt(exe 버전 리소스)도 수동으로 맞출 것

# 필름 시뮬레이션 카탈로그 (key, 표시명, 그룹). 실제 luts/<key>.cube 가 있는 것만 UI 에 노출
# (identity=None 은 LUT 미적용이라 항상 포함). 흑백 등은 .cube 를 넣으면 자동으로 다시 나타남.
FILM_SIM_CATALOG = [
    ("identity", "None", 0),
    ("provia", "Provia / Standard", 1), ("velvia", "Velvia", 1), ("astia", "Astia", 1),
    ("classic_chrome", "Classic Chrome", 2), ("classic_neg", "Classic Negative", 2),
    ("nostalgic_neg", "Nostalgic Neg", 2), ("pro_neg_hi", "PRO Neg. Hi", 2),
    ("pro_neg_std", "PRO Neg. Std", 2),
    ("eterna", "Eterna", 3), ("reala_ace", "Reala Ace", 3), ("bleach_bypass", "Bleach Bypass", 3),
    ("acros", "ACROS", 4), ("acros_ye", "ACROS + Ye", 4), ("acros_r", "ACROS + R", 4),
    ("acros_g", "ACROS + G", 4), ("monochrome", "Monochrome", 4), ("sepia", "Sepia", 4),
]


def available_film_sims():
    """카탈로그 중 luts/<key>.cube 가 실제 존재하는 것만 [{key,label,group}] 로. identity 는 항상 포함."""
    out = []
    for key, label, group in FILM_SIM_CATALOG:
        if key == "identity" or (LUTS_DIR / f"{key}.cube").exists():
            out.append({"key": key, "label": label, "group": group})
    return out

# 사이드카(폴더당 데이터) 파일/폴더 이름. 구 이름(.camraw*)은 폴더 접근 시 1회 자동 마이그레이션.
EDITS_DIR_NAME = ".filmrawsteryedits"
LIKES_FILE_NAME = ".filmrawsterylikes.json"
_OLD_SIDECARS = [(".camrawedits", EDITS_DIR_NAME), (".camrawlikes.json", LIKES_FILE_NAME)]


def _migrate_sidecars(folder: str) -> None:
    """구 사이드카 이름(.camraw*)을 신 이름(.filmrawstery*)으로 1회 이동(신 이름이 없을 때만).
    이미 신 이름이 있거나 구 이름이 없으면 아무 것도 안 함(멱등)."""
    try:
        base = Path(folder)
        for old, new in _OLD_SIDECARS:
            op, npath = base / old, base / new
            if op.exists() and not npath.exists():
                op.rename(npath)
    except Exception:
        pass

# 시작 시 자동으로 열어볼 샘플 RAF (명령줄 인자가 없을 때 사용)
DEFAULT_RAF = r"C:\Pic\x100v\128_FUJI\DSCF8035.RAF"
# DEFAULT_RAF = r"C:\Pic\x100v\131_FUJI\DSCF1039.RAF"  # 임시 비활성

# GPU 고성능(외장 GPU) 강제: Windows 그래픽 설정과 동일하게 이 실행파일(python.exe)의
# GPU 환경설정을 '고성능'으로 레지스트리에 기록한다. False 면 Windows 기본(보통 내장) 사용.
PREFER_HIGH_PERF_GPU = False


# 외장 GPU 어댑터 인덱스를 직접 지정하려면 정수로(예: 1). None=자동 탐지(전용 VRAM 최대).
GPU_ADAPTER_INDEX = None


def _list_d3d_adapters():
    """DXGI 로 어댑터 (index, name, dedicated_vram_bytes, vendor_id) 목록 반환. 실패 시 []."""
    import ctypes
    from ctypes import (POINTER, Structure, WINFUNCTYPE, byref, c_long, c_size_t,
                        c_ubyte, c_uint, c_ushort, c_void_p, c_wchar, wintypes)

    class GUID(Structure):
        _fields_ = [("Data1", c_uint), ("Data2", c_ushort), ("Data3", c_ushort), ("Data4", c_ubyte * 8)]

    class LUID(Structure):
        _fields_ = [("Low", wintypes.DWORD), ("High", c_long)]

    class DESC(Structure):
        _fields_ = [("Description", c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                    ("SubSysId", c_uint), ("Revision", c_uint), ("DedicatedVideoMemory", c_size_t),
                    ("DedicatedSystemMemory", c_size_t), ("SharedSystemMemory", c_size_t), ("AdapterLuid", LUID)]
    out = []
    try:
        iid = GUID(0x7b7166ec, 0x21c7, 0x44ae, (0xb2, 0x1a, 0xc9, 0xae, 0x32, 0x1a, 0xe3, 0x69))  # IDXGIFactory
        fac = c_void_p()
        if ctypes.windll.dxgi.CreateDXGIFactory(byref(iid), byref(fac)) != 0:
            return []
        vt = ctypes.cast(fac, POINTER(POINTER(c_void_p))).contents
        enum_adapters = WINFUNCTYPE(c_long, c_void_p, c_uint, POINTER(c_void_p))(vt[7])  # EnumAdapters
        i = 0
        while True:
            ad = c_void_p()
            if enum_adapters(fac, i, byref(ad)) != 0:
                break
            avt = ctypes.cast(ad, POINTER(POINTER(c_void_p))).contents
            get_desc = WINFUNCTYPE(c_long, c_void_p, POINTER(DESC))(avt[8])  # GetDesc
            d = DESC()
            get_desc(ad, byref(d))
            out.append((i, d.Description, int(d.DedicatedVideoMemory), int(d.VendorId)))
            i += 1
    except Exception:
        return []
    return out


def _find_discrete_adapter_index():
    """전용 VRAM 이 가장 큰 비-소프트웨어 어댑터(=외장 GPU) 인덱스. 내장만 있으면 None."""
    ads = [a for a in _list_d3d_adapters() if a[3] != 0x1414]   # 0x1414=Microsoft Basic Render 제외
    if not ads:
        return None
    best = max(ads, key=lambda a: a[2])                          # DedicatedVideoMemory 최대
    if best[2] < 512 * 1024 * 1024:                             # <512MB 면 외장 없음(내장만)으로 판단
        return None
    return best[0]


def _prefer_high_performance_gpu() -> None:
    """외장(고성능) GPU 강제 사용. ⚠️QGuiApplication 생성 *전* 호출해야 함.

    핵심: QT_D3D_ADAPTER_INDEX 로 Qt D3D11 백엔드의 어댑터를 **직접 지정**(이번 실행부터 즉시).
    보조: Windows GPU 환경설정(UserGpuPreferences)도 '고성능' 기록(다음 실행/전원관리용).
    하이브리드 노트북에서 기본값(내장 Intel)으로 도는 것을 외장(NVIDIA/AMD)으로 전환한다.
    """
    if sys.platform != "win32":
        return
    idx = GPU_ADAPTER_INDEX if GPU_ADAPTER_INDEX is not None else _find_discrete_adapter_index()
    if idx is not None:
        os.environ.setdefault("QSG_RHI_BACKEND", "d3d11")   # QT_D3D_ADAPTER_INDEX 는 D3D11 전용
        os.environ["QT_D3D_ADAPTER_INDEX"] = str(idx)
        names = {a[0]: a[1] for a in _list_d3d_adapters()}
        print(f"[gpu] 외장 GPU 강제: adapter[{idx}] {names.get(idx, '?')}")
    else:
        print("[gpu] 외장 GPU 미발견 — 기본 어댑터 사용")
    # 보조: Windows 고성능 GPU 환경설정(실패 무시)
    try:
        import winreg
        exe = sys.executable
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\DirectX\UserGpuPreferences",
                                0, winreg.KEY_READ | winreg.KEY_WRITE) as k:
            try:
                cur, _ = winreg.QueryValueEx(k, exe)
            except FileNotFoundError:
                cur = None
            if cur != "GpuPreference=2;":
                winreg.SetValueEx(k, exe, 0, winreg.REG_SZ, "GpuPreference=2;")
    except Exception:
        pass


def _find_qsb():
    """셰이더 컴파일러(qsb) 경로. PySide6 번들 qsb 우선 — venv 폴더 rename 에도 안전
    (console-script 래퍼 pyside6-qsb 는 절대경로가 박혀 폴더 이동 시 깨질 수 있음)."""
    try:
        import PySide6
        exe = "qsb.exe" if sys.platform == "win32" else "qsb"
        cand = Path(PySide6.__file__).resolve().parent / exe
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    return shutil.which("pyside6-qsb") or shutil.which("qsb")


def ensure_shader() -> None:
    """frag 셰이더들을 .qsb 로 컴파일 (이미 최신이면 건너뜀)."""
    if getattr(sys, "frozen", False):
        return  # frozen: 미리 컴파일된 .qsb 동봉, qsb.exe 미번들 + 설치 폴더 무쓰기
    qsb = None
    for name in SHADER_NAMES:
        src = SHADERS_DIR / name
        out = SHADERS_DIR / (name + ".qsb")
        if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
            continue
        if qsb is None:
            qsb = _find_qsb()
            if not qsb:
                raise RuntimeError("qsb(PySide6 셰이더 컴파일러)를 찾을 수 없습니다.")
        subprocess.run(
            [qsb, "--glsl", "120,150,300es", "--hlsl", "50", "--msl", "12",
             "-o", str(out), str(src)],
            check=True,
        )
        print(f"[shader] compiled -> {out.name}")


class RawProvider(QQuickImageProvider):
    """디코딩한 QImage 를 QML 'image://raw/...' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage()

    def set_image(self, img: QImage) -> None:
        self._img = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._img


class RawFullProvider(QQuickImageProvider):
    """GPU export 용 풀해상도 16bit(RGBA64) 헤드룸 인코딩 이미지를 'image://rawfull/...' 로 제공.

    export(GPU) 시에만 set_image 로 채워지고, 끝나면 clear()로 메모리 해제."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage()

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._img


class LutProvider(QQuickImageProvider):
    """필름 시뮬레이션 LUT 아틀라스를 'image://lut/<key>' 로 제공.

    key 는 luts/<key>.cube 파일명(확장자 제외). 모든 LUT 는 같은 크기 N 을 가정.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._atlases: dict[str, QImage] = {}
        self.size = 0  # LUT 한 변 크기 N

    def load_dir(self, luts_dir: Path) -> None:
        for cube in sorted(luts_dir.glob("*.cube")):
            lut, n = load_cube(str(cube))
            self._atlases[cube.stem] = atlas_qimage(lut, n)
            self.size = n
        print(f"[lut] {len(self._atlases)}개 로드, N={self.size}")

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        key = image_id.split("?", 1)[0]  # 쿼리스트링 제거
        return self._atlases.get(key, QImage())


class DisplayCmProvider(QQuickImageProvider):
    """디스플레이 색관리 LUT 아틀라스를 'image://displaycm/...' 로 제공(프리뷰 전용).

    현재 모니터 ICC 에서 구운 sRGB→디스플레이 3D LUT(아틀라스). 색관리 불필요(sRGB
    모니터/프로파일 없음)면 1x1 더미를 두고 size=0 → 셰이더가 미적용."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._atlas = QImage(1, 1, QImage.Format.Format_RGB888)
        self.size = 0  # LUT 한 변 N (0=항등/미적용)

    def set_atlas(self, atlas: QImage, n: int) -> None:
        if atlas is None or n <= 1:
            self._atlas = QImage(1, 1, QImage.Format.Format_RGB888)
            self.size = 0
        else:
            self._atlas = atlas
            self.size = n

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        return self._atlas


class CurveProvider(QQuickImageProvider):
    """톤 커브 1D LUT(256x1 RGB)를 'image://curve/...' 로 제공.

    R/G/B 열에 채널별 합성 커브(마스터→채널 적용)를 담는다. 셰이더가 입력 채널값으로
    해당 채널(.r/.g/.b)을 샘플링해 마스터+채널 톤커브를 합성 적용한다.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        import numpy as np
        ident = np.linspace(0.0, 1.0, 256, dtype=np.float32)
        self._img = self._make(np.stack([ident, ident, ident], axis=1))  # identity

    @staticmethod
    def _make(combined) -> QImage:
        import numpy as np
        v = np.clip(np.rint(np.asarray(combined, float) * 255.0), 0, 255).astype(np.uint8)
        if v.shape != (256, 3):
            ident = np.linspace(0, 255, 256).astype(np.uint8)
            v = np.stack([ident, ident, ident], axis=1)
        arr = np.ascontiguousarray(v.reshape(1, 256, 3))
        return QImage(arr.data, 256, 1, 256 * 3, QImage.Format.Format_RGB888).copy()

    def set_lut(self, combined) -> None:
        self._img = self._make(combined)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class StampProvider(QQuickImageProvider):
    """날짜 스탬프 오버레이(프록시 크기 RGBA)를 'image://stamp/...' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_ARGB32)
        self._img.fill(0)            # 시작 시에도 유효한 투명 텍스처

    def set_image(self, img: QImage) -> None:
        self._img = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class SkyMaskProvider(QQuickImageProvider):
    """하늘 세그멘테이션 마스크(프록시 크기 단일채널 Grayscale8)를 'image://skymask/...' 로 제공."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale8)
        self._img.fill(0)            # 시작 시에도 유효한 검정(마스크 없음) 텍스처

    def set_image(self, img: QImage) -> None:
        self._img = img

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class NrBaseProvider(QQuickImageProvider):
    """디노이즈드 중성 luma(프록시 해상도 Grayscale16)를 'image://nrbase/...' 로 제공.
    준비 전에는 1x1(셰이더가 nrOn 게이트로 무시)이라 내용 무관."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale16)
        self._img.fill(0)

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale16)
        self._img.fill(0)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class HazeProvider(QQuickImageProvider):
    """디헤이즈 투과율 맵(소형 단일채널 Grayscale8)을 'image://haze/...' 로 제공.
    기본/클리어 = 1x1 흰색(t=1, 안개 없음) → 셰이더 물리 분기가 항등이 되어 안전."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale8)
        self._img.fill(255)

    def set_image(self, img: QImage) -> None:
        self._img = img

    def clear(self) -> None:
        self._img = QImage(1, 1, QImage.Format.Format_Grayscale8)
        self._img.fill(255)

    def requestImage(self, image_id, size, requested_size):  # noqa: N802
        return self._img


class ThumbProvider(QQuickImageProvider):
    """RAF 임베드 JPEG -> 썸네일을 'image://thumb/<percent-encoded-path>' 로 제공.

    ForceAsynchronousImageLoading 으로 requestImage 가 항상 Qt 워커 스레드에서
    호출되므로 GUI 가 안 멈춘다(폴더에 파일이 많아도). QML 쪽은 ListView 로
    화면에 보이는 delegate 만 요청 -> 지연 로딩. 디코딩 결과는 경로별 캐시.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._cache = {}                 # abs_path -> QImage
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        raw = image_id.split("?", 1)[0]              # 쿼리스트링 제거(혹시 모를 대비)
        path = QUrl.fromPercentEncoding(raw.encode("utf-8"))  # encodeURIComponent 역변환
        with self._lock:
            cached = self._cache.get(path)
        if cached is not None and not cached.isNull():
            return cached
        img = self._make_thumb(path, requested_size)
        with self._lock:
            self._cache[path] = img
        return img

    @staticmethod
    def _make_thumb(path, requested_size) -> QImage:
        edge = (requested_size.width()
                if (requested_size is not None and requested_size.width() > 0) else 96)
        # 1차: RAF 내장 JPEG 안의 EXIF 썸네일(~160px, 수 KB) — 초경량/고속.
        #      EXIF/썸네일은 JPEG 선두라 앞부분 512KB 만 읽으면 충분.
        try:
            jpeg = _read_embedded_jpeg(path)
            if jpeg:
                import exifread
                tags = exifread.process_file(io.BytesIO(jpeg), details=False)
                thumb = tags.get("JPEGThumbnail")
                if thumb:
                    im = QImage()
                    if im.loadFromData(thumb):
                        ori = tags.get("Image Orientation")
                        im = ThumbProvider._apply_orientation(
                            im, ori.values[0] if ori and ori.values else 1)
                        return im.scaledToWidth(
                            edge, Qt.TransformationMode.SmoothTransformation)
        except Exception:
            pass
        # 2차(폴백): EXIF 썸네일이 없으면 풀 프리뷰를 축소 디코딩(13MP 풀디코딩 회피).
        try:
            jpeg = _read_embedded_jpeg(path, max_bytes=64 * 1024 * 1024)
            if not jpeg:
                return QImage()                      # null -> QML status=Error -> placeholder
            buf = QBuffer()
            buf.setData(jpeg)                        # 내부 QByteArray 로 복사(수명 안전)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)            # EXIF 방향 반영
            full = reader.size()
            if full.isValid() and full.width() > 0:
                h = max(1, round(edge * full.height() / full.width()))
                reader.setScaledSize(QSize(edge, h))
            img = reader.read()
            buf.close()
            return img if not img.isNull() else QImage()
        except Exception:
            return QImage()

    @staticmethod
    def _apply_orientation(img: QImage, ori: int) -> QImage:
        """EXIF Orientation(1~8)을 썸네일에 반영. IFD1 썸네일은 회전 안 된 채
        저장되므로 메인 이미지 방향값을 그대로 적용한다(세로 사진 바로 세움)."""
        if ori in (1, None):
            return img
        t = QTransform()
        if ori == 2:                       # 좌우 반전
            return img.transformed(t.scale(-1, 1))
        if ori == 3:                       # 180°
            return img.transformed(t.rotate(180))
        if ori == 4:                       # 상하 반전
            return img.transformed(t.scale(1, -1))
        if ori == 5:                       # 좌우 반전 + 90°CW
            return img.transformed(t.rotate(90).scale(-1, 1))
        if ori == 6:                       # 90°CW
            return img.transformed(t.rotate(90))
        if ori == 7:                       # 좌우 반전 + 270°CW
            return img.transformed(t.rotate(270).scale(-1, 1))
        if ori == 8:                       # 270°CW(=90°CCW)
            return img.transformed(t.rotate(270))
        return img


class PreviewProvider(QQuickImageProvider):
    """RAF 내장 풀 프리뷰 JPEG -> 큰 프리뷰를 'image://preview/<percent-encoded-path>' 로 제공.

    프리뷰 모드(PreviewWindow.qml)용. ThumbProvider 의 2차 폴백과 동일한 경로
    (내장 풀 프리뷰 JPEG 를 QImageReader.setScaledSize 로 축소 디코딩)를 쓰되,
    요청 크기(~2048px)가 커서 결과 QImage 가 장당 ~11MB → 무제한 캐시 금지.
    최근 N 개만 유지하는 LRU 로 좌/우 인접 이동 시 재디코딩을 최소화한다.
    """

    _CACHE_MAX = 5

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image,
                         QQuickImageProvider.Flag.ForceAsynchronousImageLoading)
        self._cache = OrderedDict()       # "path|edge" -> QImage (LRU)
        self._lock = threading.Lock()

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        raw = image_id.split("?", 1)[0]               # 쿼리스트링(?v=) 제거
        path = QUrl.fromPercentEncoding(raw.encode("utf-8"))
        edge = (requested_size.width()
                if (requested_size is not None and requested_size.width() > 0) else 2048)
        key = f"{path}|{edge}"
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and not cached.isNull():
                self._cache.move_to_end(key)          # 최근 사용 표시
                return cached
        img = self._make_preview(path, edge)
        with self._lock:
            self._cache[key] = img
            self._cache.move_to_end(key)
            while len(self._cache) > self._CACHE_MAX:
                self._cache.popitem(last=False)       # 가장 오래된 것 제거
        return img

    @staticmethod
    def _make_preview(path, edge) -> QImage:
        try:
            jpeg = _read_embedded_jpeg(path, max_bytes=64 * 1024 * 1024)
            if not jpeg:
                return QImage()
            buf = QBuffer()
            buf.setData(jpeg)
            buf.open(QBuffer.OpenModeFlag.ReadOnly)
            reader = QImageReader(buf, b"jpeg")
            reader.setAutoTransform(True)             # EXIF 방향 반영
            full = reader.size()
            if full.isValid() and full.width() > 0 and full.width() > edge:
                h = max(1, round(edge * full.height() / full.width()))
                reader.setScaledSize(QSize(edge, h))
            img = reader.read()
            buf.close()
            return img if not img.isNull() else QImage()
        except Exception:
            return QImage()


class Controller(QObject):
    imageChanged = Signal()
    asShotKelvinChanged = Signal()
    wbBaked = Signal()          # 재디코딩 완료(=baked WB 갱신) 알림
    curveChanged = Signal()     # 톤 커브 LUT 갱신 알림
    exportStatusChanged = Signal()
    exportProgressChanged = Signal()   # CPU export 진행률(0..1) 갱신 알림(필름 카운터 오버레이용)
    exifChanged = Signal()      # 촬영정보(EXIF) 갱신 알림
    stampChanged = Signal()     # 날짜 스탬프 오버레이 갱신 알림
    editsReady = Signal()       # 새 파일 디코딩 완료 -> QML 이 저장 편집 복원(또는 기본값 리셋)
    histogramChanged = Signal()  # 톤커브 배경 히스토그램 갱신 알림
    lensChanged = Signal()       # 렌즈 보정 on/off 변경 알림
    busyChanged = Signal()       # 디코딩(렌즈 보정 포함) 진행 중 표시
    folderChanged = Signal()     # 좌측 file explorer 현재 폴더/파일목록 갱신 알림
    likesChanged = Signal()      # 좋아요(셀렉트) 상태 변경 알림 (썸네일 하트 반영용)
    editsChanged = Signal()      # 편집 사이드카 유무 변경 알림 (썸네일 편집 배지 반영용)
    flushEdits = Signal()        # 이미지 전환 직전: QML 이 *이전* 파일로 편집 저장(플러시)
    fullChanged = Signal()       # GPU export: 풀해상도 src URL 갱신(QML Image 재로드용)
    fullReady = Signal()         # GPU export: 풀해상도 디코드 완료(QML 이 grab 준비)
    skyMaskChanged = Signal()    # 하늘 마스크 텍스처 갱신 알림(생성/클리어 모두)
    skySelected = Signal()       # 하늘 마스크 '생성 완료'만(클리어 제외) → QML 이 오버레이 자동 표시
    skyBusyChanged = Signal()    # 하늘 세그멘테이션(추론) 진행 중 표시
    segStatusChanged = Signal()  # 세그 상태 문구(예: 모델 다운로드 중) 갱신 알림
    cmChanged = Signal()         # 디스플레이 색관리 LUT 갱신 알림(모니터 전환/로드)
    hazeChanged = Signal()       # 디헤이즈 투과율 맵/대기광/conf 갱신 알림(DCP)
    nrChanged = Signal()         # 휘도 NR 베이스 텍스처/준비 상태 갱신 알림
    _renderReady = Signal(object)  # (내부) 워커 스레드 -> 메인 스레드 결과 전달
    _fullDecoded = Signal(bool)  # (내부) 풀해상도 디코드 워커 -> 메인 스레드
    _skyReady = Signal(object)   # (내부) 하늘 세그 워커 -> 메인 스레드 (seq, mask)
    _segStatusSig = Signal(str)  # (내부) 세그 워커 -> 메인 스레드 상태 문구 전달
    _exportProgressSig = Signal(float)  # (내부) export 워커 -> 메인 스레드 진행률(0..1)
    _hazeReady = Signal(object)  # (내부) 디헤이즈 추정 워커 -> 메인 스레드 (seq, (t, A, conf))
    _nrReady = Signal(object)    # (내부) NR 베이스 워커 -> 메인 스레드 (seq, 디노이즈드 luma)

    def __init__(self, provider: RawProvider, curve_provider: "CurveProvider",
                 stamp_provider: "StampProvider" = None,
                 full_provider: "RawFullProvider" = None,
                 sky_provider: "SkyMaskProvider" = None,
                 cm_provider: "DisplayCmProvider" = None,
                 haze_provider: "HazeProvider" = None,
                 nr_provider: "NrBaseProvider" = None):
        super().__init__()
        self._provider = provider
        self._cm_provider = cm_provider          # 디스플레이 색관리 LUT(프리뷰 전용)
        self._cm_n = 0                           # CM LUT 한 변 N (0=미적용)
        self._has_cm = False                     # 유효 CM LUT 존재(=광색역 모니터)
        self._cm_url = "image://displaycm/c?v=0"
        self._cm_counter = 0
        self._curve_provider = curve_provider
        self._stamp_provider = stamp_provider
        self._full_provider = full_provider     # GPU export 풀해상도 src
        self._sky_provider = sky_provider        # 하늘 마스크 텍스처
        self._haze_provider = haze_provider      # 디헤이즈 투과율 맵 텍스처(DCP)
        self._haze_url = "image://haze/h?v=0"
        self._haze_counter = 0
        self._haze_seq = 0          # 비동기 추정 순번(이미지 전환 레이스 방지)
        self._haze_t = None         # 투과율 맵(numpy float32, 소형) — CPU export 용
        self._haze_A = [1.0, 1.0, 1.0]   # 대기광(display sRGB)
        self._haze_conf = 0.0       # 추정 신뢰도(0=물리 모델 미사용 → 톤모델 폴백)
        self._nr_provider = nr_provider          # 디노이즈드 중성 luma 텍스처(휘도 NR 베이스)
        self._nr_url = "image://nrbase/n?v=0"
        self._nr_counter = 0
        self._nr_seq = 0            # 비동기 계산 순번(이미지 전환 레이스 방지)
        self._nr_ready = False      # 준비 전 셰이더 휘도 NR 무동작(nrOn 게이트)
        self._sky_url = "image://skymask/m?v=0"
        self._sky_counter = 0
        self._sky_seq = 0           # 비동기 세그/재조합 순번(오래된 결과 폐기)
        self._sky_busy = False      # 세그 추론/재조합 진행 중
        self._seg_status = ""       # 세그 상태 문구(모델 다운로드 중 등). 빈 문자열=없음
        self._sky_mask = None       # 마지막 마스크 (numpy float32 [0,1], 프록시 해상도) — CPU export 용
        self._proxy_img = None      # 마지막 프록시 QImage(세그 입력 디코드용)
        self._seg_probs = None      # 캐시된 150클래스 softmax(저해상도) — 이미지당 추론 1회
        self._seg_guide = None      # 캐시된 원본 휘도(guided filter 가이드)
        self._seg_size = None       # 캐시된 마스크 출력 크기(H,W)
        self._mask_keys = []        # 현재 선택된 클래스 그룹 key 목록
        self._full_url = "image://rawfull/f?v=0"
        self._full_counter = 0
        self._gpu_path = ""                      # GPU export 대상 파일
        self._gpu_params = {}                    # GPU export 파라미터(지오메트리 등)
        self._url = ""
        self._path = ""
        self._kelvin = None     # None = as-shot 사용
        self._tint = 0.0
        self._asshot = 5500
        self._asshot_tint = 0.0  # as-shot 추정 tint(off-locus 광원 대응)
        self._cam = []          # cam_xyz 3x3 평탄화 (9개)
        self._ref = [1.0, 1.0, 1.0]
        self._cam2srgb = []     # 카메라네이티브->선형 sRGB 매트릭스 평탄화 (9개)
        self._counter = 0
        self._curve_url = "image://curve/c?v=0"
        self._curve_counter = 0
        self._export_status = ""
        self._export_progress = 0.0   # CPU export 진행률(0..1). 워커가 _exportProgressSig 로 갱신.
        self._exporting = False
        self._exif_fields = []      # [{"label","value"}, ...] 패널용
        self._exif_summary = ""     # 오버레이용 2줄 요약
        self._stamp_text = ""       # 날짜 스탬프 텍스트 ('YY MM DD)
        self._stamp_url = "image://stamp/s?v=0"
        self._stamp_counter = 0
        self._stamp_wr = 0.0        # 스프라이트 (W,H)/짧은변 비율 — QML 오버레이 크기 산출용
        self._stamp_hr = 0.0
        self._stamp_rot = 0         # 촬영 방향(센서→업라이트 CW 회전, 0/90/180/270) — 데이트백 배치
        self._proxy_w = 0           # 마지막 프록시 크기(스탬프 레이어 재렌더용)
        self._proxy_h = 0
        self._histogram = []        # 256-bin 휘도 히스토그램(0..1 정규화)
        self._proxy_small = None    # 히스토그램 재계산용 축소 프록시(float32 0..1)
        self._lut_cache = {}        # simKey -> (lut_arr, n)
        self._lens = True           # 렌즈 보정 on/off (RAF 내장 샷별 프로파일)
        self._busy = False          # 디코딩 진행 중(스피너)
        self._render_seq = 0        # 비동기 렌더 순번(오래된 결과 폐기용)
        self._folder = ""           # 좌측 file explorer 현재 폴더
        self._files = []            # [{"name","path","isDir"}, ...] 현재 폴더 항목
        self._likes = set()         # 현재 폴더에서 좋아요된 파일명 집합
        self._likes_folder = ""     # _likes 가 속한 폴더(저장 대상 경로)
        self._like_rev = 0          # 좋아요 변경 리비전(QML 바인딩 재평가용)
        self._edited = set()        # 현재 폴더에서 편집 사이드카가 있는 파일명 집합(썸네일 배지)
        self._edited_folder = ""    # _edited 가 속한 폴더
        self._edit_rev = 0          # 편집 사이드카 유무 변경 리비전(QML 바인딩 재평가용)
        self._pending_edits = {}    # 현재 파일의 사이드카 편집(로드 시 1회 읽어 둠, editsForCurrent 반환용)
        self._ui_path = ""          # UI 가 현재 반영 중인 파일(=복원 완료된 파일). 저장은 이 경로 기준.
        self._fresh_load = False    # 새 파일 로드의 첫 디코딩 대기 중(완료 시 editsReady 발화)
        self._renderReady.connect(self._on_render_ready)
        self._fullDecoded.connect(self._on_full_decoded)
        self._skyReady.connect(self._on_sky_ready)
        self._segStatusSig.connect(self._on_seg_status)
        self._exportProgressSig.connect(self._on_export_progress)
        self._hazeReady.connect(self._on_haze_ready)
        self._nrReady.connect(self._on_nr_ready)
        # 현재 폴더 자동 감시: 디렉터리 변화 -> 디바운스 -> 재스캔(변경분 있을 때만 갱신)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(400)   # 연속 변화/중복 이벤트 합치기
        self._rescan_timer.timeout.connect(self._do_auto_rescan)
        # 마지막 탐색 폴더 영구 저장(재시작 시 복원 + 폴더 대화상자 시작 위치)
        self._settings = QSettings("FilmRawstery", "FilmRawstery")

    def _update_watcher(self, folder: str) -> None:
        old = self._watcher.directories()
        if old:
            self._watcher.removePaths(old)
        if folder and Path(folder).is_dir():
            self._watcher.addPath(folder)

    def _on_dir_changed(self, _path: str) -> None:
        self._rescan_timer.start()            # 디바운스(재시작)

    def _do_auto_rescan(self) -> None:
        if self._folder:
            self._scan_folder(self._folder, force=False)

    # ---------- 좋아요(셀렉트) 영속화: 폴더당 .filmrawsterylikes.json ----------
    @staticmethod
    def _likes_path(folder: str) -> Path:
        return Path(folder) / LIKES_FILE_NAME

    @staticmethod
    def _load_likes(folder: str) -> set:
        """폴더의 .filmrawsterylikes.json 에서 좋아요(True)된 파일명 집합을 읽음(없으면 빈 집합)."""
        try:
            _migrate_sidecars(folder)   # 구 .camraw* → 신 이름 1회 이동
            p = Controller._likes_path(folder)
            if not p.is_file():
                return set()
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {name for name, liked in data.items() if liked}
        except Exception:
            return set()

    @staticmethod
    def _save_likes(folder: str, liked_set: set) -> None:
        """좋아요 집합을 {파일명: true} JSON 으로 폴더에 저장."""
        try:
            p = Controller._likes_path(folder)
            data = {name: True for name in sorted(liked_set)}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"[likes] 저장 실패: {exc}")

    @Slot(str, result=bool)
    def isLiked(self, path: str) -> bool:  # noqa: N802 (QML 슬롯)
        return Path(path).name in self._likes

    @Slot(str)
    def toggleLike(self, path: str) -> None:  # noqa: N802 (QML 슬롯)
        """파일의 좋아요 상태를 토글하고 즉시 폴더 JSON 에 저장(크래시 안전)."""
        if not path:
            return
        name = Path(path).name
        folder = str(Path(path).parent)
        # 프리뷰 대상이 현재 탐색기 폴더와 다를 수 있으므로 해당 폴더 상태를 로드해 갱신
        if folder != self._likes_folder:
            self._likes = self._load_likes(folder)
            self._likes_folder = folder
        if name in self._likes:
            self._likes.discard(name)
        else:
            self._likes.add(name)
        self._save_likes(folder, self._likes)
        self._like_rev += 1
        self.likesChanged.emit()

    # ---------- RAF별 편집 영속화: 폴더/.filmrawsteryedits/<파일명>.json (이미지당 사이드카) ----------
    @staticmethod
    def _edits_dir(folder: str) -> Path:
        return Path(folder) / EDITS_DIR_NAME

    @staticmethod
    def _edits_path(folder: str, name: str) -> Path:
        return Controller._edits_dir(folder) / f"{name}.json"

    @staticmethod
    def _read_edits(path: str) -> dict:
        """RAF 경로의 사이드카 편집 dict 를 읽음(없거나 오류면 빈 dict)."""
        try:
            p = Path(path)
            _migrate_sidecars(str(p.parent))   # 구 .camraw* → 신 이름 1회 이동
            ep = Controller._edits_path(str(p.parent), p.name)
            if not ep.is_file():
                return {}
            with open(ep, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _load_edited_names(folder: str) -> set:
        """폴더의 .filmrawsteryedits/ 에 사이드카(<파일명>.json)가 있는 RAF 파일명 집합을 반환.
        썸네일 '편집됨' 배지 표시용(없거나 오류면 빈 집합)."""
        try:
            d = Controller._edits_dir(folder)
            if not d.is_dir():
                return set()
            return {f.name[:-5] for f in d.glob("*.json")}   # "DSCF1.RAF.json" → "DSCF1.RAF"
        except Exception:
            return set()

    @Slot(str, result=bool)
    def hasEdits(self, path: str) -> bool:  # noqa: N802 (QML 슬롯)
        """파일에 저장된 편집 사이드카가 있는지(현재 폴더 캐시 기준). 썸네일 배지용."""
        return Path(path).name in self._edited

    @Slot("QVariantMap")
    def saveEdits(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """UI 가 반영 중인 파일(_ui_path)의 편집을 사이드카 JSON 으로 저장. 크래시 안전.
        ⚠️ self._path 가 아니라 _ui_path 기준 — 새 파일 로드 중에는 _path 가 이미 바뀌었지만
        UI/editParams 는 아직 이전(반영 완료된) 파일을 나타내므로, 엉뚱한 파일에 덮어쓰기 방지."""
        if not self._ui_path:
            return
        try:
            p = Path(self._ui_path)
            d = self._edits_dir(str(p.parent))
            d.mkdir(parents=True, exist_ok=True)
            data = {k: params[k] for k in params}   # QVariantMap -> dict
            data["appVersion"] = APP_VERSION        # 이 편집을 만든 앱 버전(추후 지원/디버깅용, 참고용 기록 — 읽어서 되돌리지 않음)
            with open(d / f"{p.name}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._pending_edits = data               # 현재 파일 캐시 동기화
            # 썸네일 편집 배지 즉시 반영(현재 탐색기 폴더 파일일 때)
            if str(p.parent) == self._edited_folder and p.name not in self._edited:
                self._edited.add(p.name)
                self._edit_rev += 1
                self.editsChanged.emit()
        except Exception as exc:
            print(f"[edits] 저장 실패: {exc}")

    @Slot()
    def deleteEdits(self) -> None:  # noqa: N802 (QML 슬롯)
        """현재 UI 파일(_ui_path)의 편집 사이드카를 삭제(수동 Reset). 캐시/썸네일 배지도 갱신.
        ⚠️ saveEdits 와 동일하게 _ui_path 기준(반영 완료된 파일)."""
        if not self._ui_path:
            return
        p = Path(self._ui_path)
        try:
            ep = self._edits_path(str(p.parent), p.name)
            if ep.is_file():
                ep.unlink()
        except Exception as exc:
            print(f"[edits] 삭제 실패: {exc}")
        self._pending_edits = {}                  # 현재 파일 편집 캐시 비움
        # 썸네일 편집 배지(파일명 앰버) 해제 — 현재 폴더 파일이면 캐시에서 제거 + 리비전 증가
        if str(p.parent) == self._edited_folder and p.name in self._edited:
            self._edited.discard(p.name)
            self._edit_rev += 1
            self.editsChanged.emit()

    @Slot(str, str, str, result=str)
    def batchExportUrl(self, folder_url: str, src_path: str, ext: str) -> str:  # noqa: N802
        """배치 export 대상 파일 URL: <선택 폴더>/<원본이름>_exported.<ext>.
        경로 조립(백슬래시/URL 인코딩)은 Python 이 담당 — QML 문자열 연산의 함정 회피."""
        try:
            folder = QUrl(folder_url).toLocalFile() if folder_url else ""
            name = f"{Path(src_path).stem}_exported.{ext}"
            return QUrl.fromLocalFile(str(Path(folder) / name)).toString()
        except Exception as exc:
            print(f"[batch] URL 조립 실패: {exc}")
            return ""

    @Slot(result="QVariantMap")
    def editsForCurrent(self):  # noqa: N802 (QML 슬롯)
        """현재 파일의 저장된 편집 dict 반환(없으면 빈 dict). _load 에서 읽어둔 캐시 사용."""
        return self._pending_edits

    @Slot("QVariantList")
    def setCurve(self, curves) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 계산한 4개 채널 커브([master, r, g, b], 각 256값)로 LUT 텍스처 갱신.
        마스터→채널 합성을 256×3 LUT 로 구워 R/G/B 열에 저장."""
        import pipeline
        m, r, g, b = curves[0], curves[1], curves[2], curves[3]
        self._curve_provider.set_lut(pipeline.compose_curves(m, r, g, b))
        self._curve_counter += 1
        self._curve_url = f"image://curve/c?v={self._curve_counter}"
        self.curveChanged.emit()

    @Slot(result=QUrl)
    def suggestedExportUrl(self) -> QUrl:  # noqa: N802 (QML 슬롯)
        """Export 기본 파일명: 원본과 같은 폴더의 '<원본이름>_exported.png'."""
        if not self._path:
            return QUrl()
        p = Path(self._path)
        return QUrl.fromLocalFile(str(p.with_name(p.stem + "_exported.png")))

    @Slot(QUrl, "QVariantMap")
    def exportImage(self, file_url: QUrl, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조정값으로 풀해상도 현상 후 파일 저장 (백그라운드 스레드)."""
        if not self._path or self._exporting:
            return
        path = file_url.toLocalFile()
        pdict = {k: params[k] for k in params}     # QVariantMap -> 평범한 dict
        sky_mask = self._sky_mask                  # 요청 시점 스냅샷(export 중 마스크 변경/이미지 전환과 분리)
        haze = (self._haze_t, list(self._haze_A), self._haze_conf)   # DCP 추정 스냅샷(동일 이유)
        self._exporting = True
        self._export_progress = 0.0
        self.exportProgressChanged.emit()
        self._set_export_status("Exporting… (full resolution, may take tens of seconds)")
        threading.Thread(target=self._do_export, args=(path, pdict, sky_mask, haze),
                         daemon=True).start()

    def _do_export(self, path: str, params: dict, sky_mask=None, haze=None) -> None:
        try:
            import pipeline
            lut_arr, lut_n = None, 0
            if params.get("lutEnabled", False):
                lut_arr, lut_n = load_cube(str(LUTS_DIR / f"{params.get('simKey','identity')}.cube"))
            ident = [i / 255.0 for i in range(256)]
            curves = params.get("curves") or [ident, ident, ident, ident]
            curve_rgb = pipeline.compose_curves(*curves)
            arr = pipeline.render_full(
                self._path, self._kelvin, self._tint, params, lut_arr, lut_n, curve_rgb,
                bitdepth=int(params.get("bitDepth", 8)), sky_mask=sky_mask,
                progress=lambda f: self._exportProgressSig.emit(f), haze=haze)
            ok = pipeline.save_image(arr, path)
            msg = f"Saved: {path}" if ok else f"Save failed: {path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._exporting = False
        print(f"[export] {msg}")
        self._set_export_status(msg)   # 워커 스레드 -> 시그널은 메인으로 큐잉됨

    # ---------- GPU export: 프리뷰와 동일한 셰이더로 풀해상도 렌더(프리뷰=Export 보장) ----------
    @Slot(QUrl, "QVariantMap")
    def exportImageGpu(self, file_url: QUrl, params) -> None:  # noqa: N802 (QML 슬롯)
        """풀해상도 16bit src 를 백그라운드 디코드 → 완료 시 QML 이 GPU 셰이더로 grab/저장.
        무거운 디코드만 스레드에서; GPU 렌더/grab 은 GUI 스레드(QML)에서 수행."""
        if not self._path or self._exporting or self._full_provider is None:
            return
        self._gpu_path = file_url.toLocalFile()
        self._gpu_params = {k: params[k] for k in params}
        self._exporting = True
        self._export_progress = 0.0   # GPU 는 진행률 콜백 없음 → 0 유지(오버레이는 인디터미닛 표시)
        self.exportProgressChanged.emit()
        self._set_export_status("GPU exporting… (full-resolution decode)")
        threading.Thread(target=self._do_full_decode, daemon=True).start()

    def _do_full_decode(self) -> None:
        try:
            img, *_ = load_full(self._path, bool(self._gpu_params.get("lensCorrection", True)))
            self._full_provider.set_image(img)
            self._fullDecoded.emit(True)
        except Exception as exc:
            print(f"[export-gpu] 디코드 실패: {exc}")
            self._fullDecoded.emit(False)

    @Slot(bool)
    def _on_full_decoded(self, ok: bool) -> None:
        """메인 스레드: 풀해상도 src 준비됨 → URL 갱신(QML Image 재로드) + grab 트리거."""
        if not ok:
            self._exporting = False
            self._set_export_status("GPU export failed (decode)")
            return
        self._full_counter += 1
        self._full_url = f"image://rawfull/f?v={self._full_counter}"
        self.fullChanged.emit()   # QML srcFull.source 갱신 → 재로드
        self.fullReady.emit()     # QML: 로드 완료 시 grab

    @Slot("QImage")
    def saveGrab(self, qimg) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 grab 한 풀해상도 GPU 결과(QImage) → 지오메트리(크롭/회전) 적용 → 저장."""
        try:
            import pipeline
            import numpy as np
            arr = self._qimage_to_rgb(qimg)
            arr = pipeline._apply_geometry(arr, self._gpu_params)   # 프리뷰/CPU export 와 동일
            # 날짜 스탬프 — 크롭/회전 후 '최종 프레임'에 찍는다(CPU export·프리뷰와 동일 위치/합성).
            #   pipeFull 셰이더는 스탬프를 굽지 않음(stampOn=0). 해상도 축소 전에 찍어 상대크기 유지.
            import date_stamp
            _st = str(self._gpu_params.get("stampText", "") or "")
            if bool(self._gpu_params.get("dateStamp", False)) and _st:
                date_stamp.stamp_export(arr, _st, rot=int(self._gpu_params.get("stampRot", 0)))
            # 해상도 프리셋(긴 변) 적용 — GPU grab 은 항상 풀해상도라 여기서 축소.
            out_edge = int(self._gpu_params.get("outEdge", 0) or 0)
            if out_edge > 0 and max(arr.shape[:2]) > out_edge:
                from scipy.ndimage import zoom, gaussian_filter
                f = out_edge / float(max(arr.shape[:2]))
                x = arr.astype(np.float32)
                s = 0.5 * (1.0 / f - 1.0)
                if s > 0.4:
                    x = gaussian_filter(x, (s, s, 0.0))
                arr = np.clip(zoom(x, (f, f, 1.0), order=1) + 0.5, 0, 255).astype(np.uint8)
            ok = pipeline.save_image(arr, self._gpu_path)
            msg = f"Saved: {self._gpu_path}" if ok else f"Save failed: {self._gpu_path}"
        except Exception as exc:
            msg = f"Failed: {exc}"
        finally:
            self._exporting = False
            if self._full_provider is not None:
                self._full_provider.clear()    # 풀해상도 메모리 해제
        print(f"[export-gpu] {msg}")
        self._set_export_status(msg)

    @Slot(str)
    def refreshDisplayCm(self, device_name: str = "") -> None:  # noqa: N802 (QML 슬롯)
        """현재 모니터의 ICC 프로파일로 sRGB→디스플레이 CM LUT 재생성(프리뷰 전용).
        device_name 예: '\\\\.\\DISPLAY1'(QScreen.name()). 모니터 전환/시작 시 호출."""
        if self._cm_provider is None:
            return
        try:
            import display_cm
            icc = display_cm.display_icc_path(device_name or None)
            atlas, n = display_cm.build_cm_atlas(icc, 33)
        except Exception as exc:
            print(f"[display-cm] 실패: {exc}")
            atlas, n, icc = None, 0, None
        self._cm_provider.set_atlas(atlas, n)
        self._cm_n = self._cm_provider.size
        self._has_cm = self._cm_n > 1
        self._cm_counter += 1
        self._cm_url = f"image://displaycm/c?v={self._cm_counter}"
        self.cmChanged.emit()
        print(f"[display-cm] {'적용' if self._has_cm else '항등(sRGB/없음)'} "
              f"N={self._cm_n} dev={device_name or 'primary'} icc={icc}")

    def _get_cm_n(self) -> int:
        return self._cm_n

    def _get_has_cm(self) -> bool:
        return self._has_cm

    def _get_cm_url(self) -> str:
        return self._cm_url

    cmLutN = Property(int, _get_cm_n, notify=cmChanged)
    hasDisplayCM = Property(bool, _get_has_cm, notify=cmChanged)
    cmLutUrl = Property(str, _get_cm_url, notify=cmChanged)

    def _get_full_url(self) -> str:
        return self._full_url

    fullUrl = Property(str, _get_full_url, notify=fullChanged)

    def _set_export_status(self, s: str) -> None:
        self._export_status = s
        self.exportStatusChanged.emit()

    def _get_export_status(self) -> str:
        return self._export_status

    exportStatus = Property(str, _get_export_status, notify=exportStatusChanged)

    @Slot(float)
    def _on_export_progress(self, frac: float) -> None:
        """워커 스레드의 render_full progress 콜백 → 메인 스레드에서 진행률 갱신."""
        self._export_progress = max(0.0, min(1.0, float(frac)))
        self.exportProgressChanged.emit()

    def _get_export_progress(self) -> float:
        return self._export_progress

    # CPU export 진행률(0..1). GPU export 는 갱신 안 함(빠른 경로) → 0 유지.
    exportProgress = Property(float, _get_export_progress, notify=exportProgressChanged)

    def _get_exporting(self) -> bool:
        return self._exporting

    # 내보내는 중 여부(스피너 표시용). 상태 변경과 동시에 갱신되므로 같은 시그널로 통지.
    exporting = Property(bool, _get_exporting, notify=exportStatusChanged)

    def _get_curve_url(self) -> str:
        return self._curve_url

    curveUrl = Property(str, _get_curve_url, notify=curveChanged)

    def _get_exif(self) -> list:
        return self._exif_fields

    def _get_exif_summary(self) -> str:
        return self._exif_summary

    shootingInfo = Property("QVariantList", _get_exif, notify=exifChanged)
    shootingSummary = Property(str, _get_exif_summary, notify=exifChanged)

    def _get_stamp_url(self) -> str:
        return self._stamp_url

    def _get_stamp_text(self) -> str:
        return self._stamp_text

    def _get_stamp_wr(self) -> float:
        return self._stamp_wr

    def _get_stamp_hr(self) -> float:
        return self._stamp_hr

    def _get_stamp_rot(self) -> int:
        return self._stamp_rot

    def _get_stamp_corner(self) -> str:
        import date_stamp
        return date_stamp.corner_for_rot(self._stamp_rot)

    stampUrl = Property(str, _get_stamp_url, notify=stampChanged)
    stampText = Property(str, _get_stamp_text, notify=stampChanged)
    stampWRatio = Property(float, _get_stamp_wr, notify=stampChanged)   # 스프라이트 W/짧은변
    stampHRatio = Property(float, _get_stamp_hr, notify=stampChanged)   # 스프라이트 H/짧은변
    stampRot = Property(int, _get_stamp_rot, notify=stampChanged)       # 촬영 방향 CW 회전(export 전달)
    stampCorner = Property(str, _get_stamp_corner, notify=stampChanged)  # 데이트백 코너(프리뷰 배치)

    def _compute_histogram(self, img: QImage) -> None:
        """프록시 QImage → 히스토그램용 축소본 캐시 + 기준(입력) 히스토그램.

        프록시는 헤드룸 인코딩 카메라네이티브라, 셰이더 프론트엔드와 동일하게
        scene-linear sRGB(as-shot WB)로 디코드해 캐시하고, 기준 히스토그램은 filmic 적용본."""
        import numpy as np
        im = img.convertToFormat(QImage.Format.Format_RGB888)
        w, h = im.width(), im.height()
        if w == 0 or h == 0:
            self._proxy_small = None
            self._histogram = []
        else:
            arr = (np.frombuffer(im.constBits(), np.uint8)
                   .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3))
            step = max(1, max(h, w) // 128)          # 히스토그램용 소형 축소본(드래그 중 가벼움)
            small = arr[::step, ::step].astype(np.float32) / 255.0
            self._proxy_small = self._native_to_scenelinear(small)   # scene-linear sRGB
            self._histogram = self._hist_of(wb.filmic(self._proxy_small))  # 기준(노출0) display
        self.histogramChanged.emit()

    def _native_to_scenelinear(self, arr):
        """헤드룸 인코딩 카메라네이티브(0..1) → scene-linear sRGB(filmic 전). 셰이더 프론트엔드와 동일."""
        import numpy as np
        if not self._cam2srgb or not self._cam or not self._ref:
            return arr
        M = np.asarray(self._cam2srgb, float).reshape(3, 3)
        cam = np.asarray(self._cam, float).reshape(3, 3)
        rel = wb.rel_gain(cam, np.asarray(self._ref, float), self._asshot, 0.0)
        lin = wb.srgb_to_linear(arr) * PROXY_HEADROOM * rel    # 헤드룸 디코드 + as-shot WB
        return (lin @ M.T).astype(np.float32)                 # scene-linear sRGB

    @staticmethod
    def _hist_of(c) -> list:
        """R/G/B 3채널 히스토그램(각 256-bin)을 공통 최대값으로 정규화해 [R,G,B] 반환.
        공통 정규화라 채널 간 상대 크기 비교 가능(라이트룸식 중첩 표시)."""
        import numpy as np
        hists = [np.histogram(c[..., ch], bins=256, range=(0.0, 1.0))[0].astype(np.float32)
                 for ch in range(3)]
        m = max(float(h.max()) for h in hists)
        return [(h / m).tolist() for h in hists] if m > 0 else []

    def _get_lut(self, key):
        if key not in self._lut_cache:
            try:
                self._lut_cache[key] = load_cube(str(LUTS_DIR / f"{key}.cube"))
            except Exception:
                self._lut_cache[key] = (None, 0)
        return self._lut_cache[key]

    @Slot("QVariantMap")
    def updateHistogram(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조절값을 축소 프록시에 numpy 로 적용해 '조절 반영' 히스토그램을 재계산.
        라이트룸처럼 색 단계 전부 반영: 노출/톤/LUT/채도·바이브런스/HSL/대비/커브/컬러그레이딩/비네팅.
        (그레인은 노이즈라 제외, 로컬대비/샤프닝 등 공간 단계는 생략)"""
        if self._proxy_small is None:
            return
        import numpy as np
        import pipeline
        c = self._proxy_small.copy()                       # scene-linear sRGB
        # 노출 = scene-linear 배수 → filmic(단일 톤커브) → display. (셰이더/export 와 동일 순서)
        c = wb.filmic(c * (2.0 ** float(params.get("exposure", 0.0))))
        c = np.maximum(pipeline._tone_zones(
            c, float(params.get("highlights", 0)), float(params.get("shadows", 0)),
            float(params.get("whites", 0)), float(params.get("blacks", 0))), 0.0)
        c = np.clip(c, 0.0, 1.0)
        if params.get("lutEnabled", False):
            arr, n = self._get_lut(params.get("simKey", "identity"))
            if arr is not None:
                looked = pipeline._apply_lut3d(c, arr, n)
                st = float(params.get("lutStrength", 1.0))
                c = c * (1.0 - st) + looked * st
        # 바이브런스/채도 → HSL 컬러 믹서 (셰이더/export 와 동일: 대비 앞)
        sat = float(params.get("saturation", 0)); vib = float(params.get("vibrance", 0))
        if sat != 0.0 or vib != 0.0:
            c = pipeline._presence(c, sat, vib)
        c = pipeline._hsl_mixer(c, params.get("hslH", [0.0] * 8),
                                params.get("hslS", [0.0] * 8), params.get("hslL", [0.0] * 8))
        c = np.clip((c - 0.5) * float(params.get("contrast", 1.0)) + 0.5, 0.0, 1.0)
        curves = params.get("curves", None)
        if curves and len(curves) == 4:
            crgb = pipeline.compose_curves(*curves)
            xs = np.linspace(0.0, 1.0, 256)
            for ch in range(3):
                c[..., ch] = np.interp(c[..., ch], xs, crgb[:, ch])
        # 컬러 그레이딩(톤커브 뒤) — render_full 과 동일(hue 도→0..1)
        c = pipeline._color_grade(
            c, float(params.get("cgShadowHue", 0)) / 360.0, float(params.get("cgShadowSat", 0)),
            float(params.get("cgMidHue", 0)) / 360.0, float(params.get("cgMidSat", 0)),
            float(params.get("cgHighHue", 0)) / 360.0, float(params.get("cgHighSat", 0)),
            float(params.get("cgBalance", 0)))
        # 비네팅(정규화 좌표 — render_full 과 동일 공식). 그레인은 노이즈라 제외.
        vig = float(params.get("vignette", 0))
        if vig != 0.0:
            h2, w2 = c.shape[:2]
            yy = (np.arange(h2, dtype=np.float32) / (h2 - 1)) - 0.5
            xx = (np.arange(w2, dtype=np.float32) / (w2 - 1)) - 0.5
            rr = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2) / 0.7071
            import coeffs
            c = np.clip(c * (1.0 + vig * coeffs.VIGNETTE * pipeline._smoothstep(0.35, 1.0, rr))[..., None], 0.0, 1.0)
        self._histogram = self._hist_of(c)
        self.histogramChanged.emit()

    def _get_histogram(self) -> list:
        return self._histogram

    histogram = Property("QVariantList", _get_histogram, notify=histogramChanged)

    @Slot(QUrl)
    def load(self, file_url: QUrl) -> None:  # noqa: N802 (QML 슬롯, FileDialog QUrl)
        path = file_url.toLocalFile() if file_url.isLocalFile() else file_url.toString()
        self._load(path)

    @Slot(str)
    def loadPath(self, path: str) -> None:  # noqa: N802 (QML 슬롯, explorer 로컬 경로)
        if path:
            self._load(path)

    def _load(self, path: str) -> None:
        # 이미지 전환 직전: QML 이 *이전* 파일(self._path 아직 이전값)로 편집을 플러시 저장.
        if self._path and self._path != path:
            self.flushEdits.emit()
        self._path = path
        self._fresh_load = True   # 디코딩 완료(_on_render_ready) 시 editsReady 1회 발화 → 복원
        # 이 파일의 사이드카 편집을 1회 읽어 둠(QML editsForCurrent 가 반환).
        self._pending_edits = self._read_edits(path)
        # 저장된 WB(temp/tint)가 있으면 절대값으로 선설정 → 초기 렌더가 저장 WB 로 디코딩
        # (없으면 as-shot 으로 시작). setWb 재디코딩 이중작업 회피.
        e = self._pending_edits
        if e.get("temp") is not None:
            self._kelvin = float(e["temp"])
            self._tint = float(e.get("tint", 0.0))
        else:
            self._kelvin = None     # 새 파일은 as-shot 색온도로 시작
            self._tint = 0.0
        # 촬영정보는 경로에만 의존 -> 로드 시 1회 읽음(WB 변경 재디코딩과 무관)
        self._exif_fields, self._exif_summary = read_shooting_info(path)
        # 촬영 방향(EXIF Orientation) → 데이트백을 센서 우하단 각인처럼 회전/코너 배치(세로 사진).
        self._stamp_rot = date_stamp.rot_from_orientation(read_orientation(path))
        date_val = next((f["value"] for f in self._exif_fields
                         if f["label"] == "Date"), "")
        self._stamp_text = date_stamp.stamp_text_from_date(date_val)
        self.exifChanged.emit()
        # 좌측 file explorer 를 이 파일의 폴더로 동기화(다른 폴더 파일을 열어도 따라옴).
        parent = str(Path(path).parent)
        if parent != self._folder:
            self._scan_folder(parent)
        self._render()
        # 복원(편집 반영)은 디코딩 완료 후 _on_render_ready 에서 editsReady 로 트리거한다
        # (로드 진행 중 이전 이미지에 새 파일 편집이 잘못 반영되는 것 방지).

    # ---------- 좌측 File Explorer (폴더/파일 모델) ----------
    def _scan_folder(self, folder: str, force: bool = True) -> None:
        """폴더를 스캔해 하위폴더 + RAF 파일 목록(이름순)을 fileList 로 갱신.

        force=True: 탐색기 탐색(폴더 이동) — 항상 갱신.
        force=False: 자동 감시 재스캔 — 같은 폴더에서 목록이 그대로면 아무 것도 안 함
                     (우리 자신의 .filmrawsterylikes.json 저장이나 무관한 변화로 깜빡이지 않음).
        """
        p = Path(folder)
        try:
            entries = list(p.iterdir())
        except Exception:
            entries = []
        # 점으로 시작하는 폴더(.filmrawsteryedits 등)는 탐색기에 노출하지 않음
        dirs = sorted((e for e in entries if e.is_dir() and not e.name.startswith(".")),
                      key=lambda e: e.name.lower())
        rafs = sorted((e for e in entries
                       if e.is_file() and e.suffix.lower() == ".raf"),
                      key=lambda e: e.name.lower())
        items = [{"name": d.name, "path": str(d), "isDir": True} for d in dirs]
        items += [{"name": f.name, "path": str(f), "isDir": False} for f in rafs]
        # 자동 재스캔(force=False)인데 목록이 동일하면 갱신 생략(.json 저장 등 무시)
        if not force and str(p) == self._folder and items == self._files:
            return
        self._folder = str(p)
        self._files = items
        self._update_watcher(self._folder)   # 현재 폴더로 감시 경로 교체
        # 폴더 진입 시 좋아요 로컬 파일 체크 -> 썸네일 하트 반영
        self._likes = self._load_likes(self._folder)
        self._likes_folder = self._folder
        self._like_rev += 1
        # 편집 사이드카(.filmrawsteryedits/<name>.json) 유무 -> 썸네일 편집 배지 반영
        self._edited = self._load_edited_names(self._folder)
        self._edited_folder = self._folder
        self._edit_rev += 1
        self.folderChanged.emit()
        self.likesChanged.emit()
        self.editsChanged.emit()
        self._settings.setValue("explorer/lastFolder", self._folder)   # 재시작 복원용

    @Slot(QUrl)
    def setFolder(self, url: QUrl) -> None:  # noqa: N802 (QML 슬롯, FolderDialog)
        folder = url.toLocalFile() if url.isLocalFile() else url.toString()
        if folder:
            self._scan_folder(folder)

    @Slot(str)
    def setFolderPath(self, folder: str) -> None:  # noqa: N802 (QML 슬롯, 폴더 더블클릭)
        if folder:
            self._scan_folder(folder)

    @Slot()
    def goUp(self) -> None:  # noqa: N802 (QML 슬롯, 상위 폴더)
        if self._folder:
            parent = Path(self._folder).parent
            if str(parent) != self._folder:   # 루트면 변화 없음
                self._scan_folder(str(parent))

    def _get_folder(self) -> str:
        return self._folder

    def _get_files(self) -> list:
        return self._files

    def _get_like_rev(self) -> int:
        return self._like_rev

    def _get_edit_rev(self) -> int:
        return self._edit_rev

    def _get_folder_url(self) -> str:
        """현재 폴더의 QUrl 문자열 — FolderDialog.currentFolder 시작 위치용."""
        return QUrl.fromLocalFile(self._folder).toString() if self._folder else ""

    currentFolder = Property(str, _get_folder, notify=folderChanged)
    currentFolderUrl = Property(str, _get_folder_url, notify=folderChanged)
    fileList = Property("QVariantList", _get_files, notify=folderChanged)
    likeRevision = Property(int, _get_like_rev, notify=likesChanged)
    editsRevision = Property(int, _get_edit_rev, notify=editsChanged)

    @Slot(float, float)
    def setWb(self, kelvin: float, tint: float) -> None:  # noqa: N802 (QML 슬롯)
        """절대 색온도(Kelvin) + Tint 저장(export 용). WB 는 셰이더가 실시간 적용 →
        재디코딩 없음. 프리뷰는 QML wbGain 바인딩이 매 프레임 갱신."""
        self._kelvin = kelvin
        self._tint = tint

    @Slot(str)
    def setStampText(self, text: str) -> None:  # noqa: N802 (QML 슬롯)
        """사용자가 입력한 날짜 스탬프 텍스트 반영(재디코딩 없이 레이어만 재렌더)."""
        self._stamp_text = text or ""
        self._update_stamp_layer()

    def _update_stamp_layer(self) -> None:
        """현재 _stamp_text 로 타이트 스프라이트 + 크기 비율을 갱신. 프록시 크기와 무관(비율 기반).
        QML 이 cropClip(=최종 프레임) 위에 source-over 오버레이로 표시 → 위치/크기 최종 사이즈 기준."""
        if self._stamp_provider is None:
            return
        if self._stamp_text:
            layer, wr, hr = date_stamp.sprite_layer(self._stamp_text, rot=self._stamp_rot)
            self._stamp_wr, self._stamp_hr = wr, hr
        else:
            layer = QImage(1, 1, QImage.Format.Format_ARGB32)
            layer.fill(0)            # 투명 1x1 — sampler/Image 항상 유효하게 유지
            self._stamp_wr = self._stamp_hr = 0.0
        self._stamp_provider.set_image(layer)
        self._stamp_counter += 1
        self._stamp_url = f"image://stamp/s?v={self._stamp_counter}"
        self.stampChanged.emit()

    # ---------- 시맨틱 마스킹 (ONNX SegFormer, 복합 클래스) ----------
    #   추론 1회로 150클래스 softmax 를 캐시(_seg_probs)해 두고, 체크된 클래스들을 합산해
    #   라이브로 재조합한다(재추론 없음). 마스크 적용/조정/export 는 클래스 무관(단일 알파).
    def _get_mask_groups(self):
        import sky_seg
        return sky_seg.groups_for_qml()

    maskGroups = Property("QVariantList", _get_mask_groups, constant=True)

    # ---------- 앱 버전(제목표시줄 표시용) ----------
    def _get_app_version(self) -> str:
        return APP_VERSION

    appVersion = Property(str, _get_app_version, constant=True)

    @Slot("QVariantList")
    def setMaskClasses(self, keys) -> None:  # noqa: N802 (QML 슬롯)
        """체크된 클래스 그룹 key 목록으로 복합 마스크 생성(백그라운드). 캐시 있으면 재추론 없음.
        같은 클래스 조합의 마스크가 이미 있으면 no-op(undo/redo 등 중복 호출 방어)."""
        keys_list = [str(k) for k in keys]
        if keys_list == self._mask_keys and self._sky_mask is not None:
            return
        self._mask_keys = keys_list
        if self._proxy_img is None:
            return
        self._sky_seq += 1
        self._sky_busy = True
        self.skyBusyChanged.emit()
        threading.Thread(target=self._mask_worker,
                         args=(self._sky_seq, list(self._mask_keys)), daemon=True).start()

    @staticmethod
    def _qimage_to_rgb(qimg):
        """QImage → (H,W,3) uint8 RGB numpy (자체 소유 복사본). bytesPerLine 스트라이드 패딩 처리."""
        import numpy as np
        im = qimg.convertToFormat(QImage.Format.Format_RGB888)
        w, h = im.width(), im.height()
        if w == 0 or h == 0:
            return np.zeros((max(h, 0), max(w, 0), 3), np.uint8)
        return (np.frombuffer(im.constBits(), np.uint8)
                .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy())

    def _sky_input_rgb(self):
        """프록시(헤드룸 카메라네이티브) → 중성(노출0·as-shot WB) display sRGB uint8. 세그 입력."""
        import numpy as np
        arr = self._qimage_to_rgb(self._proxy_img).astype(np.float32) / 255.0
        disp = np.clip(wb.filmic(self._native_to_scenelinear(arr)), 0.0, 1.0)
        return (disp * 255.0 + 0.5).astype(np.uint8)

    # ---------- 디헤이즈 물리(DCP): 이미지당 1회 투과율/대기광 추정 ----------
    def _haze_worker(self, seq: int) -> None:
        """백그라운드: 중성 display 베이스(축소본)에서 (t, A, conf) 추정 → 메인으로 전달.
        입력이 노출0·as-shot 베이스라 슬라이더 값과 무관 — 디코딩당 1회면 충분."""
        import numpy as np
        import haze
        res = None
        try:
            arr = self._qimage_to_rgb(self._proxy_img).astype(np.float32) / 255.0
            step = max(1, max(arr.shape[:2]) // 640)   # 추정은 소형으로 충분(속도)
            disp = np.clip(wb.filmic(self._native_to_scenelinear(arr[::step, ::step])), 0.0, 1.0)
            res = haze.estimate(disp)
        except Exception as exc:
            print(f"[haze] 추정 실패(톤모델 폴백): {exc}")
        self._hazeReady.emit((seq, res))

    @Slot(object)
    def _on_haze_ready(self, payload) -> None:
        import numpy as np
        seq, res = payload
        if seq != self._haze_seq:
            return                       # 이미지 전환됨 → 낡은 추정 폐기
        if res is None:
            self._haze_t, self._haze_A, self._haze_conf = None, [1.0, 1.0, 1.0], 0.0
            if self._haze_provider is not None:
                self._haze_provider.clear()
        else:
            t, A, conf = res
            self._haze_t = t
            self._haze_A = [float(x) for x in A]
            self._haze_conf = float(conf)
            if self._haze_provider is not None:
                u8 = np.ascontiguousarray((np.clip(t, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
                hh, ww = u8.shape
                self._haze_provider.set_image(
                    QImage(u8.data, ww, hh, ww, QImage.Format.Format_Grayscale8).copy())
        self._haze_counter += 1
        self._haze_url = f"image://haze/h?v={self._haze_counter}"
        self.hazeChanged.emit()

    def _get_haze_url(self) -> str:
        return self._haze_url

    def _get_haze_A(self) -> list:
        return self._haze_A

    def _get_haze_conf(self) -> float:
        return self._haze_conf

    hazeUrl = Property(str, _get_haze_url, notify=hazeChanged)
    hazeA = Property("QVariantList", _get_haze_A, notify=hazeChanged)
    hazeConf = Property(float, _get_haze_conf, notify=hazeChanged)

    # ---------- 휘도 NR 베이스: 이미지당 1회 가이디드 필터 디노이즈(중성 luma) ----------
    def _nr_worker(self, seq: int) -> None:
        """백그라운드: 중성 display luma 에 가이디드 필터(coeffs.NR_*) → 셰이더 nrBase 텍스처.
        입력이 노출0·as-shot 베이스라 슬라이더와 무관 — 디코딩당 1회면 충분(haze 워커와 동형)."""
        import numpy as np
        import coeffs
        from sky_seg import _guided_filter
        res = None
        try:
            arr = self._qimage_to_rgb(self._proxy_img).astype(np.float32) / 255.0
            disp = np.clip(wb.filmic(self._native_to_scenelinear(arr)), 0.0, 1.0)
            lum = (disp @ np.array([0.299, 0.587, 0.114], np.float32)).astype(np.float32)
            res = np.clip(_guided_filter(lum, lum, coeffs.NR_RADIUS, coeffs.NR_EPS), 0.0, 1.0)
        except Exception as exc:
            print(f"[nr] 베이스 계산 실패(휘도 NR 비활성): {exc}")
        self._nrReady.emit((seq, res))

    @Slot(object)
    def _on_nr_ready(self, payload) -> None:
        import numpy as np
        seq, res = payload
        if seq != self._nr_seq:
            return                       # 이미지 전환됨 → 낡은 결과 폐기
        if res is None:
            self._nr_ready = False
            if self._nr_provider is not None:
                self._nr_provider.clear()
        else:
            if self._nr_provider is not None:
                u16 = np.ascontiguousarray((res * 65535.0 + 0.5).astype(np.uint16))
                hh, ww = u16.shape
                self._nr_provider.set_image(
                    QImage(u16.data, ww, hh, ww * 2, QImage.Format.Format_Grayscale16).copy())
            self._nr_ready = True
        self._nr_counter += 1
        self._nr_url = f"image://nrbase/n?v={self._nr_counter}"
        self.nrChanged.emit()

    def _get_nr_url(self) -> str:
        return self._nr_url

    def _get_nr_ready(self) -> bool:
        return self._nr_ready

    nrBaseUrl = Property(str, _get_nr_url, notify=nrChanged)
    nrReady = Property(bool, _get_nr_ready, notify=nrChanged)

    def _mask_worker(self, seq: int, keys) -> None:
        import os
        import numpy as np
        import sky_seg
        mask = None
        try:
            if self._seg_probs is None:                 # 이미지당 추론 1회 → 캐시
                # 모델이 아직 없으면 최초 1회 다운로드(~105MB) → 다운로드 구간에만 문구 표시.
                if not os.path.exists(sky_seg.MODEL_PATH):
                    self._segStatusSig.emit("Downloading sky model… (first use, ~105MB)")
                    sky_seg.ensure_model()               # 실제 다운로드(블로킹)
                    self._segStatusSig.emit("")          # 다운로드 끝 → 이후는 'Detecting mask…'
                rgb8 = self._sky_input_rgb()
                probs, hw = sky_seg.infer_softmax(rgb8)
                self._seg_probs = probs
                self._seg_size = hw
                self._seg_guide = (rgb8.astype(np.float32) / 255.0) @ sky_seg._LUMA
            ids = sky_seg.class_ids_for(keys)
            if ids:
                mask = sky_seg.compose_mask(self._seg_probs, self._seg_size, ids, self._seg_guide)
        except Exception as exc:
            print(f"[mask] 세그 실패: {exc}")
            self._segStatusSig.emit("")                  # 실패(다운로드 포함) 시에도 문구 제거
        self._skyReady.emit((seq, mask))

    @Slot(object)
    def _on_sky_ready(self, payload) -> None:
        import numpy as np
        seq, mask = payload
        if seq != self._sky_seq:
            return                       # 더 최신 작업 진행 중 → 폐기
        self._sky_busy = False
        self.skyBusyChanged.emit()
        if mask is None:                 # 선택 없음/실패 → 마스크 제거
            self._set_sky_mask(None)
            return
        self._set_sky_mask(mask)
        self.skySelected.emit()          # 갱신 완료 → QML 이 마스크 오버레이 자동 표시

    def _set_sky_mask(self, mask) -> None:
        """마스크(numpy [0,1] 또는 None)를 프로바이더/캐시에 반영. None=1x1 검정(sampler 유효 유지)."""
        import numpy as np
        self._sky_mask = mask            # CPU export 용(프록시 해상도 보관)
        if mask is None:
            qi = QImage(1, 1, QImage.Format.Format_Grayscale8)
            qi.fill(0)
        else:
            g = np.ascontiguousarray((np.clip(mask, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8))
            h, w = g.shape
            qi = QImage(g.data, w, h, w, QImage.Format.Format_Grayscale8).copy()
        if self._sky_provider is not None:
            self._sky_provider.set_image(qi)
        self._sky_counter += 1
        self._sky_url = f"image://skymask/m?v={self._sky_counter}"
        self.skyMaskChanged.emit()

    @Slot()
    def clearSky(self) -> None:  # noqa: N802 (QML 슬롯)
        self._clear_sky()

    def _clear_sky(self) -> None:
        """마스크 선택 해제(1x1 검정). 캐시(_seg_probs)는 유지 — 같은 이미지 재선택은 재추론 불필요."""
        self._mask_keys = []
        self._set_sky_mask(None)

    def _get_sky_url(self) -> str:
        return self._sky_url

    skyMaskUrl = Property(str, _get_sky_url, notify=skyMaskChanged)

    def _get_has_sky_mask(self) -> bool:
        return self._sky_mask is not None

    # 실제 마스크 존재 여부 — 셰이더가 invert 를 마스크 없을 때 전체 적용하지 않도록 게이팅.
    hasSkyMask = Property(bool, _get_has_sky_mask, notify=skyMaskChanged)

    def _get_sky_busy(self) -> bool:
        return self._sky_busy

    skyBusy = Property(bool, _get_sky_busy, notify=skyBusyChanged)

    @Slot(str)
    def _on_seg_status(self, s: str) -> None:
        """워커 스레드 → 메인 스레드: 세그 상태 문구 갱신(모델 다운로드 중 등)."""
        if s != self._seg_status:
            self._seg_status = s
            self.segStatusChanged.emit()

    def _get_seg_status(self) -> str:
        return self._seg_status

    segStatus = Property(str, _get_seg_status, notify=segStatusChanged)

    def _get_adjust_coeffs(self):
        import coeffs
        return coeffs.as_qml_dict()

    # 현상 계수(coeffs.py 단일 진실원) → 셰이더 uniform 주입. 값 바꾸면 프리뷰=export 동시 반영.
    adjustCoeffs = Property("QVariantMap", _get_adjust_coeffs, constant=True)

    def _get_film_sims(self):
        return available_film_sims()

    # 사용 가능한 필름시뮬 목록(luts/*.cube 존재 기준) → QML 이 콤보/simKeys/구분선 구성. 시작 시 1회.
    filmSims = Property("QVariantList", _get_film_sims, constant=True)

    @Slot(bool)
    def setLensCorrection(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """렌즈 보정 on/off (RAF 내장 샷별 프로파일, 재디코딩)."""
        if self._lens == on:
            return
        self._lens = on
        self.lensChanged.emit()
        if self._path:
            self._render()

    def _get_lens(self) -> bool:
        return self._lens

    lensCorrection = Property(bool, _get_lens, notify=lensChanged)

    def _get_busy(self) -> bool:
        return self._busy

    busy = Property(bool, _get_busy, notify=busyChanged)

    def _render(self) -> None:
        """디코딩(+렌즈 보정)을 백그라운드 스레드에서 수행. UI 안 멈추고 스피너 표시."""
        if not self._path:
            return
        self._render_seq += 1
        seq = self._render_seq
        if not self._busy:
            self._busy = True
            self.busyChanged.emit()
        args = (seq, self._path, self._lens)
        threading.Thread(target=self._render_worker, args=args, daemon=True).start()

    def _render_worker(self, seq, path, lens_on) -> None:
        try:
            res = load_proxy(path, lens_correct=lens_on)
        except Exception as exc:
            print(f"[load] 실패: {exc}")
            res = None
        self._renderReady.emit((seq, res))   # 메인 스레드로 큐잉

    @Slot(object)
    def _on_render_ready(self, payload) -> None:
        seq, res = payload
        if seq != self._render_seq:
            return                            # 더 최신 렌더 진행 중 -> 폐기(busy 유지)
        self._busy = False
        self.busyChanged.emit()
        if res is None:
            return
        img, as_shot, as_shot_tint, cam, ref, cam2srgb = res
        if self._kelvin is None:
            self._kelvin = as_shot          # as-shot 으로 디코딩됨 -> 현재값 동기화
            self._tint = as_shot_tint       # as-shot tint 도 함께 동기화(새 파일)
        self._cam = cam
        self._ref = ref
        self._cam2srgb = cam2srgb
        if as_shot != self._asshot or as_shot_tint != self._asshot_tint:
            self._asshot = as_shot
            self._asshot_tint = as_shot_tint
            self.asShotKelvinChanged.emit()
        self._provider.set_image(img)
        self._counter += 1
        # 쿼리스트링으로 캐시 무력화 -> Image 가 새로 로드됨
        self._url = f"image://raw/photo?v={self._counter}"
        self.imageChanged.emit()
        self.wbBaked.emit()              # baked kelvin/tint/matrix 갱신 알림
        self._proxy_w, self._proxy_h = img.width(), img.height()
        self._proxy_img = img            # 세그 입력 디코드용(display sRGB 변환 base)
        self._seg_probs = None           # 프록시 바뀜 → 추론 캐시 무효화(재추론 필요)
        self._seg_guide = self._seg_size = None
        prev_mask_keys = list(self._mask_keys)
        self._sky_seq += 1               # 이전 이미지의 진행 중 세그 워커 결과 폐기(전환 레이스 방지)
        self._set_sky_mask(None)         # 새 프록시 → 이전 마스크 무효(곧 재생성/복원)
        # 디헤이즈 물리(DCP): 이전 추정 무효화(준비 전엔 conf=0 → 톤모델 폴백) 후 백그라운드 재추정.
        self._haze_seq += 1
        self._haze_t, self._haze_A, self._haze_conf = None, [1.0, 1.0, 1.0], 0.0
        if self._haze_provider is not None:
            self._haze_provider.clear()
        self._haze_counter += 1
        self._haze_url = f"image://haze/h?v={self._haze_counter}"
        self.hazeChanged.emit()
        threading.Thread(target=self._haze_worker, args=(self._haze_seq,), daemon=True).start()
        # 휘도 NR 베이스: 이전 텍스처 무효화(준비 전엔 nrOn=0 → 휘도 NR 무동작) 후 재계산.
        self._nr_seq += 1
        self._nr_ready = False
        if self._nr_provider is not None:
            self._nr_provider.clear()
        self._nr_counter += 1
        self._nr_url = f"image://nrbase/n?v={self._nr_counter}"
        self.nrChanged.emit()
        threading.Thread(target=self._nr_worker, args=(self._nr_seq,), daemon=True).start()
        # 비-fresh 재디코딩(렌즈 보정·WB 커밋 등)은 editsReady(복원)를 안 거친다 → 활성 마스크가
        # 있었으면 같은 클래스로 새 프록시에 재생성(렌즈 보정은 기하 변경 → 정렬 위해 재생성 필수).
        # fresh load 는 applyEdits 가 저장본에서 복원하므로 여기선 건드리지 않는다.
        if prev_mask_keys and not self._fresh_load:
            self.setMaskClasses(prev_mask_keys)
        else:
            self._mask_keys = []
        self._update_stamp_layer()       # 날짜 스탬프 프리뷰 레이어(프록시, 우하단)
        self._compute_histogram(img)     # 톤커브 배경 히스토그램(디코딩된 프록시)
        print(f"[load] {self._path}  ({img.width()}x{img.height()})  "
              f"kelvin={self._kelvin} tint={self._tint:.2f} as_shot={as_shot}")
        # 새 파일의 첫 디코딩이 끝났을 때만 복원 트리거(WB 커밋 등 재디코딩에는 발화 안 함).
        # 이 시점에 UI 가 이 파일을 반영하게 되므로 _ui_path 갱신(저장 귀속 기준).
        if self._fresh_load:
            self._fresh_load = False
            self._ui_path = self._path
            self.editsReady.emit()

    def _get_url(self) -> str:
        return self._url

    def _get_path(self) -> str:
        return self._path

    def _get_asshot(self) -> int:
        return self._asshot

    def _get_asshot_tint(self) -> float:
        return self._asshot_tint

    def _get_cam(self) -> list:
        return self._cam

    def _get_ref(self) -> list:
        return self._ref

    def _get_cam2srgb(self) -> list:
        return self._cam2srgb

    def _get_baked_k(self) -> float:
        return float(wb.TREF)    # 프록시는 항상 TREF daylight 베이크(셰이더가 상대게인)

    def _get_baked_t(self) -> float:
        return 0.0

    imageUrl = Property(str, _get_url, notify=imageChanged)
    imagePath = Property(str, _get_path, notify=imageChanged)
    asShotKelvin = Property(int, _get_asshot, notify=asShotKelvinChanged)
    asShotTint = Property(float, _get_asshot_tint, notify=asShotKelvinChanged)
    camMatrix = Property("QVariantList", _get_cam, notify=wbBaked)
    daylightRef = Property("QVariantList", _get_ref, notify=wbBaked)
    camToSrgb = Property("QVariantList", _get_cam2srgb, notify=wbBaked)
    bakedKelvin = Property(float, _get_baked_k, notify=wbBaked)
    bakedTint = Property(float, _get_baked_t, notify=wbBaked)


def ensure_luts() -> None:
    """luts/ 에 .cube 가 없으면 근사 LUT 를 생성."""
    if getattr(sys, "frozen", False):
        return  # frozen: .cube 동봉, 설치 폴더에 절대 쓰지 않음
    if not LUTS_DIR.exists() or not any(LUTS_DIR.glob("*.cube")):
        make_luts.generate_all()


def _load_heavy_modules() -> None:
    """numpy/scipy/rawpy 등을 끌어오는 무거운 모듈을 splash 표시 *후* 로드한다.

    이 임포트들을 모듈 최상단에 두면 splash 가 뜨기 전에 다 로드돼 대기 구간이
    길어진다(특히 콜드 스타트). splash 가 보인 뒤로 미뤄 체감 시작 시간을 줄인다.
    여기서 module-global 로 바인딩하므로 이후 Controller/provider 들이 그대로 참조한다."""
    global date_stamp, make_luts, read_shooting_info, read_orientation, _read_embedded_jpeg
    global wb, atlas_qimage, load_cube, PROXY_HEADROOM, load_full, load_proxy
    import date_stamp, make_luts, wb                                  # noqa: E401
    from exif_info import read_shooting_info, read_orientation, _read_embedded_jpeg
    from lut import atlas_qimage, load_cube
    from raw_loader import PROXY_HEADROOM, load_full, load_proxy


def _show_splash(app):
    """콜드 스타트 동안 보일 가벼운 스플래시 창을 띄워 즉시 그린다.

    QQuickView 로 Splash.qml 을 로드 → 화면 중앙에 frameless 로 표시.
    이 첫 GPU 창 생성이 RHI(D3D11) 디바이스 초기화를 대부분 떠안으므로,
    뒤따르는 메인 창은 더 빨리 뜬다. processEvents 로 즉시 페인트한다.
    실패해도 앱 동작에는 영향 없도록 None 반환."""
    try:
        from PySide6.QtQuick import QQuickView
        view = QQuickView()
        view.setFlags(Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint)
        view.setResizeMode(QQuickView.ResizeMode.SizeViewToRootObject)
        view.setColor(Qt.GlobalColor.transparent)
        view.rootContext().setContextProperty("appVersion", APP_VERSION)   # setSource 전에 바인딩
        view.setSource(QUrl.fromLocalFile(str(BASE / "ui" / "Splash.qml")))
        scr = app.primaryScreen().geometry()
        view.setPosition((scr.width() - view.width()) // 2,
                         (scr.height() - view.height()) // 2)
        view.show()
        app.processEvents()    # 이벤트 루프 시작 전이라 강제로 한 번 그린다
        return view
    except Exception as exc:
        print(f"[splash] 표시 실패(무시): {exc}")
        return None


def _close_splash_when_ready(root, splash) -> None:
    """메인 창의 첫 프레임이 화면에 올라오면(frameSwapped) 스플래시를 닫는다."""
    if splash is None:
        return
    done = {"v": False}

    def _close():
        if done["v"]:
            return
        done["v"] = True
        splash.close()
        splash.deleteLater()

    # frameSwapped 는 매 프레임 발생 -> 가드로 1회만 닫는다.
    root.frameSwapped.connect(_close)
    # 혹시 frameSwapped 가 안 와도(드문 경우) 폴백으로 닫기.
    QTimer.singleShot(3000, _close)


def apply_dark_titlebar(window) -> None:
    """Windows OS 타이틀바를 다크 모드로(DWMWA_USE_IMMERSIVE_DARK_MODE)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        v = ctypes.c_int(1)
        # 20 = Win10 2004+/Win11, 19 = 이전 빌드 (둘 다 시도)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception as exc:
        print(f"[theme] 다크 타이틀바 적용 실패: {exc}")


def _print_banner() -> None:
    """터미널에서 실행할 때만 보이는 필름-스트립 시작 배너(개발자 이스터에그).
    GUI 더블클릭 실행 사용자는 콘솔이 없어 못 본다. 버전/PySide 정보는 디버깅에도 약간 유용.
    ⚠️ 어떤 경우에도 시작을 막지 않도록 전부 try/except — cp949 등 콘솔은 유니코드(●/☕) 인코딩 실패."""
    try:
        try:
            import PySide6
            pv = PySide6.__version__
        except Exception:
            pv = "?"
        py = "%d.%d.%d" % sys.version_info[:3]
        # 색은 터미널(tty)일 때만 — 파이프/리다이렉트나 VT 미지원이면 평문(이스케이프 깨짐 방지).
        color = sys.stdout.isatty()
        if color and os.name == "nt":               # Windows: VT 처리 활성화 시도
            try:
                import ctypes
                h = ctypes.windll.kernel32.GetStdHandle(-11)
                mode = ctypes.c_uint()
                color = bool(ctypes.windll.kernel32.GetConsoleMode(h, ctypes.byref(mode))) and \
                    bool(ctypes.windll.kernel32.SetConsoleMode(h, mode.value | 0x0004))
            except Exception:
                color = False
        amber = "\033[38;5;214m" if color else ""
        dim = "\033[2m" if color else ""
        rst = "\033[0m" if color else ""

        def emit(holes, sep, tail):
            sys.stdout.write(
                f"\n   {amber}{holes}{rst}\n"
                f"\n       {amber}F I L M   R A W S T E R Y{rst}"
                f"\n       {dim}slow-roasted light, developed into film{rst}\n"
                f"\n   {amber}{holes}{rst}\n"
                f"\n   {dim}v{APP_VERSION} {sep} PySide6 {pv} {sep} Python {py}{tail}{rst}\n\n")
            sys.stdout.flush()

        try:
            emit(" ".join(["●"] * 22), "·", "  ☕")          # 유니코드(필름 퍼포레이션 + 커피)
        except UnicodeEncodeError:
            emit(" ".join(["o"] * 22), "-", "")             # cp949 등 → ASCII 폴백
    except Exception:
        pass                                                 # 배너는 부가 기능 — 절대 시작을 막지 않음


def main() -> int:
    _print_banner()
    if PREFER_HIGH_PERF_GPU:
        _prefer_high_performance_gpu()   # 외장 GPU 우선(다음 실행부터). Windows 한정.

    app = QGuiApplication(sys.argv)
    splash = _show_splash(app)   # 콜드 스타트 동안 표시(아래 무거운 초기화를 덮는다)

    _load_heavy_modules()        # numpy/scipy/rawpy 등은 splash 표시 후 로드(앞 구간 단축)
    ensure_shader()
    ensure_luts()
    date_stamp.font_family()   # 번들 DSEG7 폰트 1회 등록(메인 스레드)
    engine = QQmlApplicationEngine()

    provider = RawProvider()
    engine.addImageProvider("raw", provider)

    lut_provider = LutProvider()
    lut_provider.load_dir(LUTS_DIR)
    engine.addImageProvider("lut", lut_provider)

    curve_provider = CurveProvider()
    engine.addImageProvider("curve", curve_provider)

    stamp_provider = StampProvider()
    engine.addImageProvider("stamp", stamp_provider)

    thumb_provider = ThumbProvider()
    engine.addImageProvider("thumb", thumb_provider)

    preview_provider = PreviewProvider()
    engine.addImageProvider("preview", preview_provider)

    full_provider = RawFullProvider()
    engine.addImageProvider("rawfull", full_provider)

    sky_provider = SkyMaskProvider()
    engine.addImageProvider("skymask", sky_provider)

    cm_provider = DisplayCmProvider()
    engine.addImageProvider("displaycm", cm_provider)

    haze_provider = HazeProvider()
    engine.addImageProvider("haze", haze_provider)

    nr_provider = NrBaseProvider()
    engine.addImageProvider("nrbase", nr_provider)

    controller = Controller(provider, curve_provider, stamp_provider, full_provider,
                            sky_provider, cm_provider, haze_provider, nr_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    ctx.setContextProperty("lutN", lut_provider.size)

    engine.load(QUrl.fromLocalFile(str(BASE / "ui" / "Main.qml")))
    if not engine.rootObjects():
        return -1

    root = engine.rootObjects()[0]
    apply_dark_titlebar(root)                      # OS 타이틀바 다크 모드(Windows)
    _close_splash_when_ready(root, splash)         # 메인 창 첫 프레임에 스플래시 닫기

    # 디스플레이 색관리(프리뷰 전용): 현재 모니터 ICC 로 CM LUT 생성 + 모니터 전환 시 재생성.
    def _refresh_cm(*_):
        scr = root.screen()
        controller.refreshDisplayCm(scr.name() if scr is not None else "")
    _refresh_cm()
    root.screenChanged.connect(_refresh_cm)

    # 시작 동작: 인자로 파일/폴더를 주면 그대로 따르고, 인자가 없으면 **사진을 자동 로드하지 않고**
    # 폴더만 탐색기에 연다(사용자가 직접 더블클릭해 로드). 기본 폴더 = 개발 샘플 폴더(있으면) > Pictures.
    if len(sys.argv) > 1:
        start_path = sys.argv[1]
        if Path(start_path).is_file():
            controller.load(QUrl.fromLocalFile(start_path))   # load() 가 부모폴더도 scan
        elif Path(start_path).is_dir():
            controller.setFolderPath(start_path)
        else:
            print(f"[init] 시작 경로 없음: {start_path}")
            controller.setFolderPath(str(Path(start_path).parent))
    else:
        # 마지막 탐색 폴더 복원(종료 후에도 기억) > 개발 샘플 폴더 > Pictures.
        last = str(QSettings("FilmRawstery", "FilmRawstery")
                   .value("explorer/lastFolder", "") or "")
        if last and Path(last).is_dir():
            start_folder = last
        elif Path(DEFAULT_RAF).is_file():
            start_folder = str(Path(DEFAULT_RAF).parent)      # 개발 샘플 폴더만 열기(자동 로드 X)
        else:
            from PySide6.QtCore import QStandardPaths
            pics = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation)
            start_folder = pics or str(Path.home())
        controller.setFolderPath(start_folder)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

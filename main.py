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
                            QSize, Qt, QTimer, Signal, Slot, QUrl)
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
SHADER_NAMES = ["adjust.frag", "blur.frag", "convert.frag"]
LUTS_DIR = BASE / "luts"

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
    exifChanged = Signal()      # 촬영정보(EXIF) 갱신 알림
    stampChanged = Signal()     # 날짜 스탬프 오버레이 갱신 알림
    editsReady = Signal()       # 새 파일 디코딩 완료 -> QML 이 저장 편집 복원(또는 기본값 리셋)
    histogramChanged = Signal()  # 톤커브 배경 히스토그램 갱신 알림
    lensChanged = Signal()       # 렌즈 보정 on/off 변경 알림
    busyChanged = Signal()       # 디코딩(렌즈 보정 포함) 진행 중 표시
    folderChanged = Signal()     # 좌측 file explorer 현재 폴더/파일목록 갱신 알림
    likesChanged = Signal()      # 좋아요(셀렉트) 상태 변경 알림 (썸네일 하트 반영용)
    flushEdits = Signal()        # 이미지 전환 직전: QML 이 *이전* 파일로 편집 저장(플러시)
    fullChanged = Signal()       # GPU export: 풀해상도 src URL 갱신(QML Image 재로드용)
    fullReady = Signal()         # GPU export: 풀해상도 디코드 완료(QML 이 grab 준비)
    _renderReady = Signal(object)  # (내부) 워커 스레드 -> 메인 스레드 결과 전달
    _fullDecoded = Signal(bool)  # (내부) 풀해상도 디코드 워커 -> 메인 스레드

    def __init__(self, provider: RawProvider, curve_provider: "CurveProvider",
                 stamp_provider: "StampProvider" = None,
                 full_provider: "RawFullProvider" = None):
        super().__init__()
        self._provider = provider
        self._curve_provider = curve_provider
        self._stamp_provider = stamp_provider
        self._full_provider = full_provider     # GPU export 풀해상도 src
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
        self._exporting = False
        self._exif_fields = []      # [{"label","value"}, ...] 패널용
        self._exif_summary = ""     # 오버레이용 2줄 요약
        self._stamp_text = ""       # 날짜 스탬프 텍스트 ('YY MM DD)
        self._stamp_url = "image://stamp/s?v=0"
        self._stamp_counter = 0
        self._proxy_w = 0           # 마지막 프록시 크기(스탬프 레이어 재렌더용)
        self._proxy_h = 0
        self._histogram = []        # 256-bin 휘도 히스토그램(0..1 정규화)
        self._proxy_small = None    # 히스토그램 재계산용 축소 프록시(float32 0..1)
        self._lut_cache = {}        # simKey -> (lut_arr, n)
        self._lens = True           # X100V 렌즈 프로파일 보정 on/off
        self._busy = False          # 디코딩 진행 중(스피너)
        self._render_seq = 0        # 비동기 렌더 순번(오래된 결과 폐기용)
        self._folder = ""           # 좌측 file explorer 현재 폴더
        self._files = []            # [{"name","path","isDir"}, ...] 현재 폴더 항목
        self._likes = set()         # 현재 폴더에서 좋아요된 파일명 집합
        self._likes_folder = ""     # _likes 가 속한 폴더(저장 대상 경로)
        self._like_rev = 0          # 좋아요 변경 리비전(QML 바인딩 재평가용)
        self._pending_edits = {}    # 현재 파일의 사이드카 편집(로드 시 1회 읽어 둠, editsForCurrent 반환용)
        self._ui_path = ""          # UI 가 현재 반영 중인 파일(=복원 완료된 파일). 저장은 이 경로 기준.
        self._fresh_load = False    # 새 파일 로드의 첫 디코딩 대기 중(완료 시 editsReady 발화)
        self._renderReady.connect(self._on_render_ready)
        self._fullDecoded.connect(self._on_full_decoded)
        # 현재 폴더 자동 감시: 디렉터리 변화 -> 디바운스 -> 재스캔(변경분 있을 때만 갱신)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_dir_changed)
        self._rescan_timer = QTimer(self)
        self._rescan_timer.setSingleShot(True)
        self._rescan_timer.setInterval(400)   # 연속 변화/중복 이벤트 합치기
        self._rescan_timer.timeout.connect(self._do_auto_rescan)

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

    # ---------- 좋아요(셀렉트) 영속화: 폴더당 .camrawlikes.json ----------
    @staticmethod
    def _likes_path(folder: str) -> Path:
        return Path(folder) / ".camrawlikes.json"

    @staticmethod
    def _load_likes(folder: str) -> set:
        """폴더의 .camrawlikes.json 에서 좋아요(True)된 파일명 집합을 읽음(없으면 빈 집합)."""
        try:
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

    # ---------- RAF별 편집 영속화: 폴더/.camrawedits/<파일명>.json (이미지당 사이드카) ----------
    @staticmethod
    def _edits_dir(folder: str) -> Path:
        return Path(folder) / ".camrawedits"

    @staticmethod
    def _edits_path(folder: str, name: str) -> Path:
        return Controller._edits_dir(folder) / f"{name}.json"

    @staticmethod
    def _read_edits(path: str) -> dict:
        """RAF 경로의 사이드카 편집 dict 를 읽음(없거나 오류면 빈 dict)."""
        try:
            p = Path(path)
            ep = Controller._edits_path(str(p.parent), p.name)
            if not ep.is_file():
                return {}
            with open(ep, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

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
            with open(d / f"{p.name}.json", "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._pending_edits = data               # 현재 파일 캐시 동기화
        except Exception as exc:
            print(f"[edits] 저장 실패: {exc}")

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

    @Slot(QUrl, "QVariantMap")
    def exportImage(self, file_url: QUrl, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조정값으로 풀해상도 현상 후 파일 저장 (백그라운드 스레드)."""
        if not self._path or self._exporting:
            return
        path = file_url.toLocalFile()
        pdict = {k: params[k] for k in params}     # QVariantMap -> 평범한 dict
        self._exporting = True
        self._set_export_status("내보내는 중… (풀해상도, 수십 초 걸릴 수 있음)")
        threading.Thread(target=self._do_export, args=(path, pdict), daemon=True).start()

    def _do_export(self, path: str, params: dict) -> None:
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
                bitdepth=int(params.get("bitDepth", 8)))
            ok = pipeline.save_image(arr, path)
            msg = f"저장됨: {path}" if ok else f"저장 실패: {path}"
        except Exception as exc:
            msg = f"실패: {exc}"
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
        self._set_export_status("GPU 내보내는 중… (풀해상도 디코드)")
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
            self._set_export_status("GPU export 실패(디코드)")
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
            im = qimg.convertToFormat(QImage.Format.Format_RGB888)
            w, h = im.width(), im.height()
            import numpy as np
            arr = (np.frombuffer(im.constBits(), np.uint8)
                   .reshape(h, im.bytesPerLine())[:, :w * 3].reshape(h, w, 3).copy())
            arr = pipeline._apply_geometry(arr, self._gpu_params)   # 프리뷰/CPU export 와 동일
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
            msg = f"저장됨: {self._gpu_path}" if ok else f"저장 실패: {self._gpu_path}"
        except Exception as exc:
            msg = f"실패: {exc}"
        finally:
            self._exporting = False
            if self._full_provider is not None:
                self._full_provider.clear()    # 풀해상도 메모리 해제
        print(f"[export-gpu] {msg}")
        self._set_export_status(msg)

    def _get_full_url(self) -> str:
        return self._full_url

    fullUrl = Property(str, _get_full_url, notify=fullChanged)

    def _set_export_status(self, s: str) -> None:
        self._export_status = s
        self.exportStatusChanged.emit()

    def _get_export_status(self) -> str:
        return self._export_status

    exportStatus = Property(str, _get_export_status, notify=exportStatusChanged)

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

    stampUrl = Property(str, _get_stamp_url, notify=stampChanged)
    stampText = Property(str, _get_stamp_text, notify=stampChanged)

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
            c = np.clip(c * (1.0 + vig * 0.8 * pipeline._smoothstep(0.35, 1.0, rr))[..., None], 0.0, 1.0)
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
                     (우리 자신의 .camrawlikes.json 저장이나 무관한 변화로 깜빡이지 않음).
        """
        p = Path(folder)
        try:
            entries = list(p.iterdir())
        except Exception:
            entries = []
        # 점으로 시작하는 폴더(.camrawedits 등)는 탐색기에 노출하지 않음
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
        self.folderChanged.emit()
        self.likesChanged.emit()

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

    currentFolder = Property(str, _get_folder, notify=folderChanged)
    fileList = Property("QVariantList", _get_files, notify=folderChanged)
    likeRevision = Property(int, _get_like_rev, notify=likesChanged)

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
        """현재 _stamp_text + 프록시 크기로 스탬프 프리뷰 레이어를 갱신."""
        if self._stamp_provider is None:
            return
        if self._stamp_text and self._proxy_w and self._proxy_h:
            layer = date_stamp.preview_layer_qimage(
                self._stamp_text, self._proxy_w, self._proxy_h)
        else:
            layer = QImage(1, 1, QImage.Format.Format_ARGB32)
            layer.fill(0)            # 투명 1x1 — 셰이더 sampler 항상 유효하게 유지
        self._stamp_provider.set_image(layer)
        self._stamp_counter += 1
        self._stamp_url = f"image://stamp/s?v={self._stamp_counter}"
        self.stampChanged.emit()

    @Slot(bool)
    def setLensCorrection(self, on: bool) -> None:  # noqa: N802 (QML 슬롯)
        """X100V 렌즈 보정 on/off (재디코딩)."""
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
    global date_stamp, make_luts, read_shooting_info, _read_embedded_jpeg
    global wb, atlas_qimage, load_cube, PROXY_HEADROOM, load_full, load_proxy
    import date_stamp, make_luts, wb                                  # noqa: E401
    from exif_info import read_shooting_info, _read_embedded_jpeg
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
        view.setSource(QUrl.fromLocalFile(str(BASE / "Splash.qml")))
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


def main() -> int:
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

    controller = Controller(provider, curve_provider, stamp_provider, full_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    ctx.setContextProperty("lutN", lut_provider.size)

    engine.load(QUrl.fromLocalFile(str(BASE / "Main.qml")))
    if not engine.rootObjects():
        return -1

    root = engine.rootObjects()[0]
    apply_dark_titlebar(root)                      # OS 타이틀바 다크 모드(Windows)
    _close_splash_when_ready(root, splash)         # 메인 창 첫 프레임에 스플래시 닫기

    # 시작 경로: 인자 > 개발용 샘플(있으면) > 사용자 Pictures 폴더(배포 기본)
    if len(sys.argv) > 1:
        start_path = sys.argv[1]
    elif Path(DEFAULT_RAF).is_file():
        start_path = DEFAULT_RAF
    else:
        from PySide6.QtCore import QStandardPaths
        pics = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.PicturesLocation)
        start_path = pics or str(Path.home())
    if Path(start_path).is_file():
        controller.load(QUrl.fromLocalFile(start_path))   # load() 가 부모폴더도 scan
    elif Path(start_path).is_dir():
        controller.setFolderPath(start_path)              # 폴더(Pictures 등)를 탐색기로 열기
    else:
        print(f"[init] 시작 경로 없음: {start_path}")
        controller.setFolderPath(str(Path(start_path).parent))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

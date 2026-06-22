"""RAW 에디터 최소 동작 스켈레톤.

  RAF 디코딩(rawpy) -> 프록시 QImage -> QML ShaderEffect(GPU) 파이프라인.
  프래그먼트 셰이더는 시작 시 번들 qsb 로 자동 컴파일한다(ensure_shader).

사용:
  pip install -r requirements.txt
  python main.py [선택: 열어둘 RAF 경로]
"""

import io
import json
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

import date_stamp
import make_luts
from exif_info import read_shooting_info, _read_embedded_jpeg
import wb
from lut import atlas_qimage, load_cube
from raw_loader import PROXY_HEADROOM, load_proxy

BASE = Path(__file__).resolve().parent
SHADERS_DIR = BASE / "shaders"
SHADER_NAMES = ["adjust.frag", "blur.frag", "convert.frag"]
LUTS_DIR = BASE / "luts"

# 시작 시 자동으로 열어볼 샘플 RAF (명령줄 인자가 없을 때 사용)
DEFAULT_RAF = r"C:\Pic\x100v\128_FUJI\DSCF8035.RAF"
# DEFAULT_RAF = r"C:\Pic\x100v\131_FUJI\DSCF1039.RAF"  # 임시 비활성


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

    R=G=B=커브 출력. 셰이더에서 채널값으로 샘플링(linear)해 톤커브 적용.
    """

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._img = self._make([i / 255.0 for i in range(256)])  # identity

    @staticmethod
    def _make(lut) -> QImage:
        import numpy as np
        v = np.clip(np.rint(np.asarray(lut, float) * 255.0), 0, 255).astype(np.uint8)
        if v.shape[0] != 256:
            v = np.linspace(0, 255, 256).astype(np.uint8)
        arr = np.zeros((1, 256, 3), np.uint8)
        arr[0, :, 0] = v; arr[0, :, 1] = v; arr[0, :, 2] = v
        arr = np.ascontiguousarray(arr)
        return QImage(arr.data, 256, 1, 256 * 3, QImage.Format.Format_RGB888).copy()

    def set_lut(self, lut) -> None:
        self._img = self._make(lut)

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
    stampReset = Signal()       # 새 파일 로드 -> 입력필드를 EXIF 기본값으로 되돌림
    histogramChanged = Signal()  # 톤커브 배경 히스토그램 갱신 알림
    lensChanged = Signal()       # 렌즈 보정 on/off 변경 알림
    busyChanged = Signal()       # 디코딩(렌즈 보정 포함) 진행 중 표시
    folderChanged = Signal()     # 좌측 file explorer 현재 폴더/파일목록 갱신 알림
    likesChanged = Signal()      # 좋아요(셀렉트) 상태 변경 알림 (썸네일 하트 반영용)
    _renderReady = Signal(object)  # (내부) 워커 스레드 -> 메인 스레드 결과 전달

    def __init__(self, provider: RawProvider, curve_provider: "CurveProvider",
                 stamp_provider: "StampProvider" = None):
        super().__init__()
        self._provider = provider
        self._curve_provider = curve_provider
        self._stamp_provider = stamp_provider
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
        self._renderReady.connect(self._on_render_ready)
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

    @Slot("QVariantList")
    def setCurve(self, lut) -> None:  # noqa: N802 (QML 슬롯)
        """QML 이 계산한 256개 커브 값(0..1)으로 LUT 텍스처 갱신."""
        self._curve_provider.set_lut(lut)
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
            curve = list(params.get("curve", [i / 255.0 for i in range(256)]))
            arr = pipeline.render_full(
                self._path, self._kelvin, self._tint, params, lut_arr, lut_n, curve)
            ok = pipeline.save_image(arr, path)
            msg = f"저장됨: {path}" if ok else f"저장 실패: {path}"
        except Exception as exc:
            msg = f"실패: {exc}"
        finally:
            self._exporting = False
        print(f"[export] {msg}")
        self._set_export_status(msg)   # 워커 스레드 -> 시그널은 메인으로 큐잉됨

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
        import numpy as np
        lum = c[..., 0] * 0.299 + c[..., 1] * 0.587 + c[..., 2] * 0.114
        hist = np.histogram(lum, bins=256, range=(0.0, 1.0))[0].astype(np.float32)
        m = float(hist.max())
        return (hist / m).tolist() if m > 0 else []

    def _get_lut(self, key):
        if key not in self._lut_cache:
            try:
                self._lut_cache[key] = load_cube(str(LUTS_DIR / f"{key}.cube"))
            except Exception:
                self._lut_cache[key] = (None, 0)
        return self._lut_cache[key]

    @Slot("QVariantMap")
    def updateHistogram(self, params) -> None:  # noqa: N802 (QML 슬롯)
        """현재 조절값(exposure/contrast/hi·sh·wh·bl/필름시뮬/커브)을 축소 프록시에
        numpy 로 적용해 '조절 반영' 히스토그램을 재계산. (공간/그레인 단계는 생략)"""
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
        c = np.clip((c - 0.5) * float(params.get("contrast", 1.0)) + 0.5, 0.0, 1.0)
        curve = params.get("curve", None)
        if curve and len(curve) >= 2:
            xs = np.linspace(0.0, 1.0, len(curve))
            cl = np.asarray(curve, dtype=np.float32)
            for ch in range(3):
                c[..., ch] = np.interp(c[..., ch], xs, cl)
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
        self._path = path
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
        self.stampReset.emit()   # 입력필드를 새 파일의 EXIF 날짜로 동기화

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
        dirs = sorted((e for e in entries if e.is_dir()), key=lambda e: e.name.lower())
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
    if not LUTS_DIR.exists() or not any(LUTS_DIR.glob("*.cube")):
        make_luts.generate_all()


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
    ensure_shader()
    ensure_luts()

    app = QGuiApplication(sys.argv)
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

    controller = Controller(provider, curve_provider, stamp_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    ctx.setContextProperty("lutN", lut_provider.size)

    engine.load(QUrl.fromLocalFile(str(BASE / "Main.qml")))
    if not engine.rootObjects():
        return -1

    apply_dark_titlebar(engine.rootObjects()[0])   # OS 타이틀바 다크 모드(Windows)

    # 명령줄 인자가 있으면 그 경로, 없으면 기본 샘플을 자동 로드
    start_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAF
    if Path(start_path).is_file():
        controller.load(QUrl.fromLocalFile(start_path))   # load() 가 부모폴더도 scan
    else:
        print(f"[init] 시작 파일 없음: {start_path}")
        controller.setFolderPath(str(Path(start_path).parent))  # 폴더라도 열어둠

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

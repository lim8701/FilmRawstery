"""RAW 에디터 최소 동작 스켈레톤.

  RAF 디코딩(rawpy) -> 프록시 QImage -> QML ShaderEffect(GPU) 파이프라인.
  프래그먼트 셰이더는 시작 시 번들 qsb 로 자동 컴파일한다(ensure_shader).

사용:
  pip install -r requirements.txt
  python main.py [선택: 열어둘 RAF 경로]
"""

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, Signal, Slot, QUrl
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickImageProvider

import date_stamp
import make_luts
from exif_info import read_shooting_info
from lut import atlas_qimage, load_cube
from raw_loader import load_proxy

BASE = Path(__file__).resolve().parent
SHADERS_DIR = BASE / "shaders"
SHADER_NAMES = ["adjust.frag", "blur.frag"]
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
        self._cam = []          # cam_xyz 3x3 평탄화 (9개)
        self._ref = [1.0, 1.0, 1.0]
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
        """프록시 QImage → 히스토그램용 축소본 캐시 + 기준(입력) 히스토그램."""
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
            self._proxy_small = arr[::step, ::step].astype(np.float32) / 255.0
            self._histogram = self._hist_of(self._proxy_small)
        self.histogramChanged.emit()

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
        c = self._proxy_small.copy()
        c = np.clip(c * (2.0 ** float(params.get("exposure", 0.0))), 0.0, 1.0)
        c = np.clip(pipeline._tone_zones(
            c, float(params.get("highlights", 0)), float(params.get("shadows", 0)),
            float(params.get("whites", 0)), float(params.get("blacks", 0))), 0.0, 1.0)
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
    def load(self, file_url: QUrl) -> None:
        path = file_url.toLocalFile() if file_url.isLocalFile() else file_url.toString()
        self._path = path
        self._kelvin = None     # 새 파일은 as-shot 색온도로 시작
        self._tint = 0.0
        # 촬영정보는 경로에만 의존 -> 로드 시 1회 읽음(WB 변경 재디코딩과 무관)
        self._exif_fields, self._exif_summary = read_shooting_info(path)
        date_val = next((f["value"] for f in self._exif_fields
                         if f["label"] == "Date"), "")
        self._stamp_text = date_stamp.stamp_text_from_date(date_val)
        self.exifChanged.emit()
        self._render()
        self.stampReset.emit()   # 입력필드를 새 파일의 EXIF 날짜로 동기화

    @Slot(float, float)
    def setWb(self, kelvin: float, tint: float) -> None:  # noqa: N802 (QML 슬롯)
        """절대 색온도(Kelvin) + Tint 변경 -> 재디코딩."""
        if self._kelvin == kelvin and self._tint == tint:
            return  # 변화 없음 -> 재디코딩 생략 (초기화 시 중복 디코딩 방지)
        self._kelvin = kelvin
        self._tint = tint
        if self._path:
            self._render()

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

    def _render(self) -> None:
        try:
            img, as_shot, cam, ref = load_proxy(
                self._path, kelvin=self._kelvin, tint=self._tint)
        except Exception as exc:  # 스켈레톤: 콘솔에만 출력
            print(f"[load] 실패: {exc}")
            return
        if self._kelvin is None:
            self._kelvin = as_shot       # as-shot 으로 디코딩됨 -> 현재값 동기화
        self._cam = cam
        self._ref = ref
        if as_shot != self._asshot:
            self._asshot = as_shot
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

    def _get_cam(self) -> list:
        return self._cam

    def _get_ref(self) -> list:
        return self._ref

    def _get_baked_k(self) -> float:
        return float(self._kelvin if self._kelvin is not None else self._asshot)

    def _get_baked_t(self) -> float:
        return float(self._tint)

    imageUrl = Property(str, _get_url, notify=imageChanged)
    imagePath = Property(str, _get_path, notify=imageChanged)
    asShotKelvin = Property(int, _get_asshot, notify=asShotKelvinChanged)
    camMatrix = Property("QVariantList", _get_cam, notify=wbBaked)
    daylightRef = Property("QVariantList", _get_ref, notify=wbBaked)
    bakedKelvin = Property(float, _get_baked_k, notify=wbBaked)
    bakedTint = Property(float, _get_baked_t, notify=wbBaked)


def ensure_luts() -> None:
    """luts/ 에 .cube 가 없으면 근사 LUT 를 생성."""
    if not LUTS_DIR.exists() or not any(LUTS_DIR.glob("*.cube")):
        make_luts.generate_all()


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

    controller = Controller(provider, curve_provider, stamp_provider)
    ctx = engine.rootContext()
    ctx.setContextProperty("controller", controller)
    ctx.setContextProperty("lutN", lut_provider.size)

    engine.load(QUrl.fromLocalFile(str(BASE / "Main.qml")))
    if not engine.rootObjects():
        return -1

    # 명령줄 인자가 있으면 그 경로, 없으면 기본 샘플을 자동 로드
    start_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAF
    if Path(start_path).is_file():
        controller.load(QUrl.fromLocalFile(start_path))
    else:
        print(f"[init] 시작 파일 없음: {start_path}")

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

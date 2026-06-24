# FilmRawstery.spec — PyInstaller onedir build
# Build:  .\.venv\Scripts\pyinstaller.exe FilmRawstery.spec --noconfirm
# 디버그 시 아래 CONSOLE = True 로 두고 빌드(누락 DLL/플러그인 에러를 콘솔로 확인),
# 검증 후 CONSOLE = False 로 바꿔 재빌드(릴리스: 콘솔창 없음).
from PyInstaller.utils.hooks import collect_data_files

CONSOLE = False

datas = [
    ("Main.qml", "."),
    ("Splash.qml", "."),
    ("PreviewWindow.qml", "."),
    ("CurveEditor.qml", "."),
    ("shaders", "shaders"),     # .frag + 미리 컴파일된 .qsb
    ("luts", "luts"),           # *.cube (+ _approx_backup)
    ("fonts", "fonts"),         # DSEG7Classic-Bold.ttf
]
datas += collect_data_files("rawpy")   # libraw 네이티브 DLL

hiddenimports = [
    "scipy.ndimage",            # lazy `from scipy.ndimage import ...`
    # numpy / rawpy / exifread 는 일반 import → 자동 탐지
]

excludes = [  # 미사용 Qt 모듈 제거(용량↓). 문제 생기면 먼저 excludes 완화
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebView", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtSerialPort", "PySide6.QtSerialBus", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtHelp", "PySide6.QtDesigner", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects", "PySide6.QtSpatialAudio",
    "tkinter",
    # ⚠️ unittest/pydoc/test 는 제외 금지 — numpy.testing 이 scipy 경유로 unittest 를 끌어옴
    # QtNetwork / QtOpenGL 도 제외 금지(Qt Quick 가 끌어올 수 있음)
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FilmRawstery",
    debug=False,
    strip=False,
    upx=False,          # UPX off — Qt DLL 손상 방지
    console=CONSOLE,
    icon=None,
    contents_directory="lib",   # onedir 하위폴더 이름(기본 _internal → lib)
)
coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,
    name="FilmRawstery",   # → dist/FilmRawstery/
)

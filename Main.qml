import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: win
    visible: true
    visibility: Window.Maximized   // 시작 시 최대화(타이틀바·작업표시줄 유지)
    width: 1280
    height: 820                     // 복원(restore) 시 사용할 크기
    title: "FILM RAWSTERY"
    color: "#1a1a1a"

    // === 종료 확인 ===
    // X/Alt+F4 로 닫을 때 한 번 확인. allowClose 가 true 면(확인 후) 그대로 닫힘.
    property bool allowClose: false
    onClosing: function(close) {
        if (!win.allowClose) {
            close.accepted = false
            quitDialog.open()
        }
    }

    // === WB 실시간 프리뷰 (드래그 중) ===
    // baked 색온도로 디코딩된 프록시에 "baked->target" 상대 게인만 셰이더로 입힌다.
    // 손을 떼면 target 색온도로 재디코딩(확정)하고 게인은 (1,1,1) 로 수렴 -> 이중적용 없음.
    // 유도상 daylight_ref·기준온도가 약분돼 카메라 매트릭스(camMatrix)만 있으면 계산 가능.
    readonly property int wbTRef: 5500

    // 촬영정보 플로팅 패널 표시 여부 (I 키로 토글)
    property bool infoOverlay: true
    Shortcut { sequence: "I"; onActivated: win.infoOverlay = !win.infoOverlay }

    // 날짜 스탬프(필름 데이트백) 표시 여부 (D 키로 토글). 기본 off.
    property bool dateStamp: false
    Shortcut { sequence: "D"; onActivated: win.dateStamp = !win.dateStamp }

    // 좌측 File Explorer 패널 표시 여부 (B 키로 토글)
    property bool showExplorer: true
    Shortcut { sequence: "B"; onActivated: win.showExplorer = !win.showExplorer }

    // 원본 비교(Before/After): true 면 프리뷰가 무편집 현상(dispPre)으로 전환. 버튼/\ 키로 토글.
    property bool compareOn: false
    Shortcut { sequence: "\\"; onActivated: win.compareOn = !win.compareOn }

    // 디스플레이 색관리(프리뷰 전용 sRGB→모니터 색역 보정, display_cm.py). Ctrl+Shift+M 토글. export 불변.
    property bool displayCM: true

    // 클리핑 경고 오버레이(프리뷰): 하이라이트=빨강 / 섀도=파랑. J 키로 토글(라이트룸과 동일).
    property bool clipWarn: false
    Shortcut { sequence: "J"; onActivated: win.clipWarn = !win.clipWarn }
    // Undo / Redo (편집 스냅샷)
    Shortcut { sequences: [StandardKey.Undo]; onActivated: win.undo() }                    // Ctrl+Z
    Shortcut { sequences: [StandardKey.Redo, "Ctrl+Shift+Z"]; onActivated: win.redo() }    // Ctrl+Y / Ctrl+Shift+Z
    // 우측 패널 전환: Edit / Crop·Geometry / Masking
    Shortcut { sequence: "Ctrl+1"; onActivated: win.activePanel = 0 }
    Shortcut { sequence: "Ctrl+2"; onActivated: win.activePanel = 1 }
    Shortcut { sequence: "Ctrl+3"; onActivated: win.activePanel = 2 }

    // 디스플레이 색관리(프리뷰 전용 sRGB→모니터 색역 보정) 토글.
    Shortcut { sequence: "Ctrl+Shift+M"; onActivated: win.displayCM = !win.displayCM }


    // 컬러 그레이딩 Hue 슬라이더 위에 두는 무지개 스펙트럼 막대(슬라이더 위치↔색상 가이드).
    // (네이티브 스타일은 Slider.background 커스터마이즈 미지원 → 별도 막대로 표시)
    component HueBar: Rectangle {
        implicitHeight: 8; radius: 4
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0;    color: "#ff0000" }
            GradientStop { position: 0.1667; color: "#ffff00" }
            GradientStop { position: 0.3333; color: "#00ff00" }
            GradientStop { position: 0.5;    color: "#00ffff" }
            GradientStop { position: 0.6667; color: "#0000ff" }
            GradientStop { position: 0.8333; color: "#ff00ff" }
            GradientStop { position: 1.0;    color: "#ff0000" }
        }
    }

    // 우측 활성 패널: 0=Edit, 1=Crop/Rotate/Geometry (우측 끝 세로 셀렉터 바로 전환)
    property int activePanel: 0

    // HSL 컬러 믹서: 8색상대(45° 균등) × 색상/채도/휘도 조정값(-1..1), 선택 대역 hslBand.
    property var hslH: [0, 0, 0, 0, 0, 0, 0, 0]
    property var hslS: [0, 0, 0, 0, 0, 0, 0, 0]
    property var hslL: [0, 0, 0, 0, 0, 0, 0, 0]
    property int hslBand: 0
    function setHslBandValue(arr, v) {     // arr: "hslH"|"hslS"|"hslL" — 선택 대역값 갱신
        var a = win[arr].slice(); a[win.hslBand] = v; win[arr] = a
    }
    function resetHsl() {
        win.hslH = [0, 0, 0, 0, 0, 0, 0, 0]
        win.hslS = [0, 0, 0, 0, 0, 0, 0, 0]
        win.hslL = [0, 0, 0, 0, 0, 0, 0, 0]
    }

    // Edit 패널 섹션 접기 상태(인덱스=표시순서: 0필름 1라이트 2톤커브 3WB 4컬러 5컬러믹서
    // 11컬러그레이딩 6디테일&비네팅 7그레인 8샤프닝 12노이즈리덕션 9렌즈 10날짜). 헤더 클릭으로 토글.
    // 기본 접힘: 5 Color Mixer, 8 Sharpening, 12 Noise Reduction, 9 Lens, 11 Color Grading.
    property var secOpen: [true, true, true, true, true, false, true, true, false, false, true, false, false]
    function toggleSec(i) { var a = secOpen.slice(); a[i] = !a[i]; secOpen = a }

    // 마스크 선택영역 오버레이 표시(프리뷰 전용 시각화)
    property bool showSkyMask: false
    // 현재 체크된 마스크 클래스 그룹 key 목록(복합 선택). 토글 시 라이브 재조합.
    property var maskKeys: []
    // 사이드카 복원으로 마스크 재생성 중 — 완료 시 오버레이 자동 표시 억제(로드 시 갑자기 적색 방지).
    property bool _maskRestore: false
    function toggleMaskKey(key, on) {
        var a = maskKeys.slice()
        var i = a.indexOf(key)
        if (on && i < 0) a.push(key)
        else if (!on && i >= 0) a.splice(i, 1)
        maskKeys = a
        maskApplyTimer.restart()   // 디바운스: 빠른 연속 토글을 한 번의 재조합으로 합침
    }
    // 체크박스 토글 코얼레싱 — 마지막 토글 후 잠깐 뒤 한 번만 세그/재조합 실행(스레드 폭증 방지).
    Timer {
        id: maskApplyTimer
        interval: 220
        onTriggered: controller.setMaskClasses(win.maskKeys)
    }

    // 마스킹 조정 슬라이더(라벨 + -1..1 슬라이더 + 더블클릭 리셋 + 조정 중 오버레이 끄기) 공용 컴포넌트.
    // host=win 주입(인라인 컴포넌트는 외부 id 접근 불가). value 는 alias 라 id 로 .value 참조 가능.
    component SkySlider: ColumnLayout {
        id: skyRoot
        property alias value: skySld.value
        property string label: ""
        property string suffix: ""
        property real defaultValue: 0.0
        property var host: null
        Layout.fillWidth: true
        spacing: 2
        Label {
            text: skyRoot.label + ":  " + skySld.value.toFixed(2) + skyRoot.suffix
            color: "white"
        }
        Slider {
            id: skySld
            Layout.fillWidth: true
            from: -1.0; to: 1.0; value: 0.0
            property real _lastPressMs: 0
            property bool _pendingReset: false
            onPressedChanged: {
                if (pressed) _pendingReset = skyRoot.host.isDblPress(skySld)
                else if (_pendingReset) { value = skyRoot.defaultValue; _pendingReset = false }
            }
            onMoved: skyRoot.host.showSkyMask = false   // 조정 중엔 오버레이 끄고 실제 효과 보기
        }
    }

    // 마스킹 조정 직렬화 — 단일 진실원(아래 키 목록). editParams/exportParams/applyEdits/editSaveWatch
    // 가 이 헬퍼로 파생되어 한 곳만 고치면 됨(예전엔 네 곳에 따로 나열 → 누락 시 저장/export 불일치).
    readonly property var skyAdjustKeys: ["skyExp", "skyTemp", "skyTint", "skySat", "skyHi",
                                          "skyShadows", "skyTexture", "skyClarity", "skyDehaze"]
    function _skySlider(key) {
        switch (key) {
        case "skyExp": return skyExpSlider;        case "skyTemp": return skyTempSlider
        case "skyTint": return skyTintSlider;      case "skySat": return skySatSlider
        case "skyHi": return skyHiSlider;          case "skyShadows": return skyShadowsSlider
        case "skyTexture": return skyTextureSlider; case "skyClarity": return skyClaritySlider
        case "skyDehaze": return skyDehazeSlider
        }
        return null
    }
    // 저장/export 페이로드 조각(maskKeys + invert + 9개 조정값). render_full 은 maskKeys 무시.
    function skyEditParams() {
        var o = { "maskKeys": win.maskKeys, "skyInvert": skyInvertCheck.checked }
        for (var i = 0; i < win.skyAdjustKeys.length; i++) {
            var k = win.skyAdjustKeys[i]; o[k] = win._skySlider(k).value
        }
        return o
    }
    // 복원: 조정값 + 선택 클래스. 마스크는 클래스로부터 재생성(setMaskClasses → 재추론).
    function applySkyEdits(p) {
        for (var i = 0; i < win.skyAdjustKeys.length; i++) {
            var k = win.skyAdjustKeys[i]; win._skySlider(k).value = win._ev(p, k, 0.0)
        }
        skyInvertCheck.checked = win._ev(p, "skyInvert", false)
        win.showSkyMask = false
        var mk = win._ev(p, "maskKeys", []); win.maskKeys = mk.slice()
        if (mk.length > 0) { win._maskRestore = true; controller.setMaskClasses(mk) }
        else controller.clearSky()
    }

    // === 회전/크롭(지오메트리) 상태 — 프리뷰 뷰변환과 export numpy 양쪽에서 사용 ===
    property int quarterTurns: 0        // 90° 단위 회전 (⟳ CW +1, ⟲ CCW -1, mod 4)
    // 종횡비 콤보 인덱스 -> 비율(가로/세로). aspectCombo 모델과 순서 일치.
    // [0]원본=원본비율잠금(cropAspect 에서 viewport.cA), [1]자유=무잠금(-1), 나머지=고정비율.
    readonly property var aspectRatios: [-1, -1, 1.0, 1.5, 4.0 / 3.0, 16.0 / 9.0, 1.25]
    // 최종 크롭 비율(가로/세로). 방향 토글이 '세로'면 역수. <=0 이면 무잠금(자유).
    readonly property real cropAspect: {
        var idx = aspectCombo.currentIndex
        if (idx === 0) return viewport.cA        // 원본 = 원본 비율(캔버스 비율) 잠금
        var r = win.aspectRatios[idx]
        if (r <= 0) return -1                    // 자유 = 무잠금
        return cropPortraitBtn.checked ? (1.0 / r) : r
    }
    // 자유 크롭 박스(정규화, 캔버스A=flip+90+스트레이튼 후 기준). 기본 = 전체.
    property real cropX: 0.0
    property real cropY: 0.0
    property real cropW: 1.0
    property real cropH: 1.0
    function resetCropRect() { cropX = 0; cropY = 0; cropW = 1; cropH = 1 }
    // 박스 설정: [0,1] 및 최소크기(0.05)로 클램프.
    function setCropRect(nx, ny, nw, nh) {
        var minS = 0.05
        nw = Math.max(minS, Math.min(1.0, nw))
        nh = Math.max(minS, Math.min(1.0, nh))
        nx = Math.max(0.0, Math.min(1.0 - nw, nx))
        ny = Math.max(0.0, Math.min(1.0 - nh, ny))
        win.cropX = nx; win.cropY = ny; win.cropW = nw; win.cropH = nh
    }
    // 종횡비 잠금이면 그 비율의 중앙 최대 박스로 맞춤(자유/원본이면 유지).
    function applyCropAspect() {
        var a = win.cropAspect
        if (a <= 0) return
        var kn = a / Math.max(0.0001, viewport.cA)   // 정규화 가로/세로(nw/nh)
        var nw, nh
        if (kn >= 1.0) { nw = 1.0; nh = 1.0 / kn }
        else { nh = 1.0; nw = kn }
        win.setCropRect((1.0 - nw) / 2.0, (1.0 - nh) / 2.0, nw, nh)
    }
    // 새 파일 로드 / 전체 초기화 시 회전·크롭·지오메트리 리셋.
    function resetGeometry() {
        rotAngleSlider.value = 0.0
        win.quarterTurns = 0
        flipHBtn.checked = false
        flipVBtn.checked = false
        aspectCombo.currentIndex = 0
        cropLandscapeBtn.checked = true
        win.resetCropRect()
        geoVSlider.value = 0
        geoHSlider.value = 0
        geoScaleSlider.value = 100
    }

    // === RAF별 편집 자동 저장/복원 (사이드카 .filmrawsteryedits/<파일명>.json) ===
    property bool _applying: false       // 복원 중 — 자동저장/WB 재디코딩 억제
    function _hasSavedEdits() { var e = controller.editsForCurrent(); return e && e.v !== undefined }

    // 저장 페이로드(원시 컨트롤 값) — 저장/복원의 단일 진실원.
    function editParams() {
        var o = {
            "v": 1,
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "temp": tempSlider.value, "tint": tintSlider.value,
            // simKey(문자열)=복원 기준(목록 변동에 안전). simIndex=구버전 폴백용 유지.
            "simKey": (simCombo.currentIndex >= 0 && simCombo.currentIndex < win.simKeys.length)
                      ? win.simKeys[simCombo.currentIndex] : "identity",
            "simIndex": simCombo.currentIndex, "simStrength": simStrengthSlider.value,
            "texture": texSlider.value, "clarity": claritySlider.value, "dehaze": dehazeSlider.value,
            "vibrance": vibSlider.value, "saturation": satSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "sharpenAmt": sharpAmtSlider.value, "sharpenRadius": sharpRadiusSlider.value,
            "sharpenDetail": sharpDetailSlider.value, "sharpenMask": sharpMaskSlider.value,
            "lumaNR": lumaNrSlider.value, "colorNR": colorNrSlider.value,
            "lensCorrection": lensCheck.checked, "dateStamp": win.dateStamp, "stampText": stampField.text,
            "curves": curveEditor.channelPoints,
            "quarterTurns": win.quarterTurns, "rotateAngle": rotAngleSlider.value,
            "flipH": flipHBtn.checked, "flipV": flipVBtn.checked,
            "aspectIndex": aspectCombo.currentIndex, "cropLandscape": cropLandscapeBtn.checked,
            "cropX": win.cropX, "cropY": win.cropY, "cropW": win.cropW, "cropH": win.cropH,
            "geoV": geoVSlider.value, "geoH": geoHSlider.value, "geoScale": geoScaleSlider.value
        }
        // 마스킹(선택 클래스 + 로컬 조정) 병합. 마스크 픽셀은 저장 안 함 — 로드 시 클래스로 재생성.
        var sk = win.skyEditParams()
        for (var k in sk) o[k] = sk[k]
        return o
    }
    function _ev(p, k, d) { return p[k] !== undefined ? p[k] : d }

    // 저장된 편집을 컨트롤에 복원. 반드시 _applying 가드 안에서 호출(자동저장/WB 재디코딩 방지).
    function applyEdits(p) {
        expSlider.value = _ev(p, "exposure", 0.0); conSlider.value = _ev(p, "contrast", 1.0)
        hiSlider.value = _ev(p, "highlights", 0.0); shSlider.value = _ev(p, "shadows", 0.0)
        whSlider.value = _ev(p, "whites", 0.0); blSlider.value = _ev(p, "blacks", 0.0)
        tempSlider.value = _ev(p, "temp", controller.asShotKelvin)
        tintSlider.value = _ev(p, "tint", controller.asShotTint)
        // 필름시뮬 복원: simKey(문자열) 우선 → 현재 목록에서 인덱스 역추적(없으면 None). 구버전은 simIndex.
        var _sk = _ev(p, "simKey", "")
        var _si
        if (_sk !== "") { _si = win.simKeys.indexOf(_sk); if (_si < 0) _si = 0 }   // 목록에 없는 LUT → None
        else { _si = _ev(p, "simIndex", 0); if (_si < 0 || _si >= win.simKeys.length) _si = 0 }
        simCombo.currentIndex = _si
        simStrengthSlider.value = _ev(p, "simStrength", 1.0)
        texSlider.value = _ev(p, "texture", 0.0); claritySlider.value = _ev(p, "clarity", 0.0)
        dehazeSlider.value = _ev(p, "dehaze", 0.0)
        vibSlider.value = _ev(p, "vibrance", 0.0); satSlider.value = _ev(p, "saturation", 0.0)
        win.hslH = _ev(p, "hslH", [0,0,0,0,0,0,0,0]).slice()
        win.hslS = _ev(p, "hslS", [0,0,0,0,0,0,0,0]).slice()
        win.hslL = _ev(p, "hslL", [0,0,0,0,0,0,0,0]).slice()
        cgShHueSlider.value = _ev(p, "cgShadowHue", 0.0); cgShSatSlider.value = _ev(p, "cgShadowSat", 0.0)
        cgMidHueSlider.value = _ev(p, "cgMidHue", 0.0); cgMidSatSlider.value = _ev(p, "cgMidSat", 0.0)
        cgHiHueSlider.value = _ev(p, "cgHighHue", 0.0); cgHiSatSlider.value = _ev(p, "cgHighSat", 0.0)
        cgBalanceSlider.value = _ev(p, "cgBalance", 0.0)
        hslHueSlider.value = win.hslH[win.hslBand]
        hslSatSlider.value = win.hslS[win.hslBand]
        hslLumSlider.value = win.hslL[win.hslBand]
        vignetteSlider.value = _ev(p, "vignette", 0.0)
        grainSlider.value = _ev(p, "grainAmt", 0.0); grainSizeSlider.value = _ev(p, "grainSize", 0.5)
        sharpAmtSlider.value = _ev(p, "sharpenAmt", 0.0); sharpRadiusSlider.value = _ev(p, "sharpenRadius", 1.0)
        sharpDetailSlider.value = _ev(p, "sharpenDetail", 0.25); sharpMaskSlider.value = _ev(p, "sharpenMask", 0.0)
        lumaNrSlider.value = _ev(p, "lumaNR", 0.0); colorNrSlider.value = _ev(p, "colorNR", 0.0)
        win.dateStamp = _ev(p, "dateStamp", false)
        stampField.text = _ev(p, "stampText", controller.stampText)
        // 프로그램으로 text 를 바꾸면 onTextEdited 가 안 불리므로 직접 push(스탬프 렌더 갱신).
        controller.setStampText(stampField.text)
        controller.setLensCorrection(_ev(p, "lensCorrection", true))
        var cp = _ev(p, "curves", null)
        if (cp) { curveEditor.setChannelPoints(cp); controller.setCurve(curveEditor.allLuts()) }
        else curveEditor.resetAll()
        win.quarterTurns = _ev(p, "quarterTurns", 0); rotAngleSlider.value = _ev(p, "rotateAngle", 0.0)
        flipHBtn.checked = _ev(p, "flipH", false); flipVBtn.checked = _ev(p, "flipV", false)
        var land = _ev(p, "cropLandscape", true)
        cropLandscapeBtn.checked = land; cropPortraitBtn.checked = !land
        aspectCombo.currentIndex = _ev(p, "aspectIndex", 0)
        win.setCropRect(_ev(p,"cropX",0.0), _ev(p,"cropY",0.0), _ev(p,"cropW",1.0), _ev(p,"cropH",1.0))
        geoVSlider.value = _ev(p, "geoV", 0); geoHSlider.value = _ev(p, "geoH", 0)
        geoScaleSlider.value = _ev(p, "geoScale", 100)
        win.applySkyEdits(p)   // 마스킹(선택 클래스 + 조정) 복원 — 마스크는 클래스로부터 재생성
    }

    // 하늘(로컬) 조정 초기화 — 슬라이더 + 마스크 + 오버레이. 새 파일 로드/Reset 에서 호출.
    function resetSky() {
        skyExpSlider.value = 0.0; skyTempSlider.value = 0.0; skyTintSlider.value = 0.0
        skySatSlider.value = 0.0; skyHiSlider.value = 0.0; skyShadowsSlider.value = 0.0
        skyTextureSlider.value = 0.0; skyClaritySlider.value = 0.0; skyDehazeSlider.value = 0.0
        skyInvertCheck.checked = false
        win.showSkyMask = false
        win.maskKeys = []
        controller.clearSky()
    }

    // 전체 초기화(편집 + 지오메트리). 수동 Reset 버튼 & 저장본 없는 파일 로드에서 호출.
    function resetAllEdits() {
        expSlider.value = 0.0; conSlider.value = 1.0
        hiSlider.value = 0.0; shSlider.value = 0.0; whSlider.value = 0.0; blSlider.value = 0.0
        texSlider.value = 0.0; claritySlider.value = 0.0; dehazeSlider.value = 0.0
        satSlider.value = 0.0; vibSlider.value = 0.0
        win.resetHsl(); hslHueSlider.value = 0.0; hslSatSlider.value = 0.0; hslLumSlider.value = 0.0
        cgShHueSlider.value = 0.0; cgShSatSlider.value = 0.0; cgMidHueSlider.value = 0.0
        cgMidSatSlider.value = 0.0; cgHiHueSlider.value = 0.0; cgHiSatSlider.value = 0.0
        cgBalanceSlider.value = 0.0
        sharpAmtSlider.value = 0.0; sharpRadiusSlider.value = 1.0
        sharpDetailSlider.value = 0.25; sharpMaskSlider.value = 0.0
        lumaNrSlider.value = 0.0; colorNrSlider.value = 0.0
        vignetteSlider.value = 0.0; grainSlider.value = 0.0; grainSizeSlider.value = 0.5
        tempSlider.value = controller.asShotKelvin; tintSlider.value = controller.asShotTint
        simCombo.currentIndex = 0; simStrengthSlider.value = 1.0
        curveEditor.resetAll()
        win.resetGeometry()
        win.resetSky()
    }

    // 수동 Reset 버튼: 모든 편집 초기화 + 사이드카 삭제(+썸네일 파일명 앰버 해제).
    // 자동저장(editSaveWatch→editSaveTimer)이 기본값 사이드카를 다시 만들지 않도록 _applying 으로
    // 감싸고(변경 onChanged 동기 억제) 보류 중 저장 타이머도 멈춘다. _applying 중 막힌 WB/커브는
    // paste/undo 와 동일하게 직접 반영. 리셋 상태는 undo 스텝으로 push(되돌리면 사이드카 복원).
    function resetAndClearEdits() {
        win._applying = true
        win.resetAllEdits()
        win._applying = false
        editSaveTimer.stop()                              // 보류 중 자동저장 취소(기본값 재생성 방지)
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.deleteEdits()                          // 사이드카 삭제 + 썸네일 배지(파일명 앰버) 해제
        win.refreshHistogram()
        win.histPush(JSON.stringify(win.editParams()))    // 리셋 상태 = undo 스텝(undo 시 편집 복원)
    }

    // ===== 편집 복사/붙여넣기 (이미지 간) =====
    // 클립보드는 editParams 스냅샷(JSON 딥카피 — 이후 원본 편집 변경에 영향 안 받게).
    property var _editClipboard: null
    // excludeWb=true 면 temp/tint 를 뺀 스냅샷 → 붙여넣을 때 대상의 WB 유지.
    // 사진별 고유 항목은 복사에서 제외 → 붙여넣을 때 대상 이미지의 값이 유지됨.
    // (date stamp + geometry. WB·Tint 는 excludeWb 일 때 추가 제외)
    readonly property var _copyExclude: ["dateStamp", "stampText",
        "quarterTurns", "rotateAngle", "flipH", "flipV", "aspectIndex", "cropLandscape",
        "cropX", "cropY", "cropW", "cropH", "geoV", "geoH", "geoScale"]
    function copyEdits(excludeWb) {
        if (controller.imagePath === "") return
        var snap = JSON.parse(JSON.stringify(win.editParams()))
        var ex = win._copyExclude.slice()
        if (excludeWb) { ex.push("temp"); ex.push("tint") }
        for (var i = 0; i < ex.length; i++) delete snap[ex[i]]
        _editClipboard = snap
    }
    function pasteEdits() {
        if (!_editClipboard || controller.imagePath === "") return
        // 현재 이미지 편집값을 기준으로, 클립보드에 담긴 항목만 덮어씀 →
        // 복사에서 제외된 항목(date stamp·geometry·WB)은 대상 값 그대로 유지.
        var p = win.editParams()
        for (var k in _editClipboard) p[k] = _editClipboard[k]
        win._applying = true
        win.applyEdits(p)
        win._applying = false
        // _applying 중엔 WB 커밋이 막히므로 직접 반영(export 가 쓰는 _kelvin/_tint 갱신).
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())   // 붙여넣은 편집을 현재 이미지 사이드카에 저장
        win.refreshHistogram()
    }

    // ===== Undo / Redo (편집 스냅샷 스택) =====
    // editParams() JSON 스냅샷을 쌓는다. 자동저장(editSaveTimer 디바운스) 시점마다 1개 push
    // → 슬라이더 드래그 1회 = 1 스텝(중간 프레임 무시). 새 파일 로드 시 baseline 으로 리셋.
    property var undoHist: []           // JSON 문자열 배열
    property int undoPos: -1            // 현재 상태 인덱스
    readonly property bool canUndo: undoPos > 0
    readonly property bool canRedo: undoPos >= 0 && undoPos < undoHist.length - 1

    function histReset(snapStr) { win.undoHist = [snapStr]; win.undoPos = 0 }
    function histPush(snapStr) {
        if (win.undoPos >= 0 && win.undoHist[win.undoPos] === snapStr) return   // 변화 없음
        var h = win.undoHist.slice(0, win.undoPos + 1)                          // redo 꼬리 버림
        h.push(snapStr)
        if (h.length > 100) h = h.slice(h.length - 100)                         // 상한
        win.undoHist = h; win.undoPos = h.length - 1
    }
    // 스냅샷 적용(undo/redo 공통) — paste 와 동일 경로: _applying 가드로 자동저장/WB 재디코딩
    // 억제 후 WB·커브 직접 반영 + 사이드카 저장 + 히스토그램 갱신.
    function applySnapshot(snapStr) {
        var p = JSON.parse(snapStr)
        win._applying = true
        win.applyEdits(p)
        win._applying = false
        controller.setWb(tempSlider.value, tintSlider.value)
        controller.setCurve(curveEditor.allLuts())
        controller.saveEdits(win.editParams())
        win.refreshHistogram()
    }
    function undo() { if (win.canUndo) { win.undoPos = win.undoPos - 1; win.applySnapshot(win.undoHist[win.undoPos]) } }
    function redo() { if (win.canRedo) { win.undoPos = win.undoPos + 1; win.applySnapshot(win.undoHist[win.undoPos]) } }

    // 자동저장: 편집 변화를 단일 바인딩(editSaveWatch)으로 감지 → 디바운스 후 1회 저장.
    function scheduleSave() {
        if (win._applying || controller.imagePath === "") return
        editSaveTimer.restart()
    }
    Timer {
        id: editSaveTimer
        interval: 500
        onTriggered: {
            if (win._applying || controller.imagePath === "") return
            var snap = win.editParams()
            controller.saveEdits(snap)
            win.histPush(JSON.stringify(snap))   // 커밋된 편집 1개 = undo 스텝 1개
        }
    }
    // 모든 편집 컨트롤 값을 참조 → 무엇이든 바뀌면 바인딩 재평가 → onChanged 로 저장 예약.
    property var editSaveWatch: [
        expSlider.value, conSlider.value, hiSlider.value, shSlider.value, whSlider.value, blSlider.value,
        tempSlider.value, tintSlider.value, simCombo.currentIndex, simStrengthSlider.value,
        texSlider.value, claritySlider.value, dehazeSlider.value, vibSlider.value, satSlider.value,
        win.hslH, win.hslS, win.hslL,
        cgShHueSlider.value, cgShSatSlider.value, cgMidHueSlider.value, cgMidSatSlider.value,
        cgHiHueSlider.value, cgHiSatSlider.value, cgBalanceSlider.value,
        vignetteSlider.value, grainSlider.value, grainSizeSlider.value,
        sharpAmtSlider.value, sharpRadiusSlider.value, sharpDetailSlider.value, sharpMaskSlider.value,
        lumaNrSlider.value, colorNrSlider.value,
        lensCheck.checked, win.dateStamp, stampField.text, curveEditor.channelPoints,
        win.quarterTurns, rotAngleSlider.value, flipHBtn.checked, flipVBtn.checked,
        aspectCombo.currentIndex, cropLandscapeBtn.checked,
        win.cropX, win.cropY, win.cropW, win.cropH,
        geoVSlider.value, geoHSlider.value, geoScaleSlider.value,
        JSON.stringify(win.skyEditParams())   // 마스킹 값 변경 추적(함수 내부 프로퍼티 읽기까지 추적됨)
    ]
    onEditSaveWatchChanged: win.scheduleSave()

    // 히스토그램 갱신 watcher: 색 단계(채도/바이브런스/HSL/컬러그레이딩)+비네팅이 바뀌면 재계산.
    // (노출/톤/대비/커브 슬라이더는 자체 onMoved 로 이미 refreshHistogram 호출함)
    property var histWatch: [
        satSlider.value, vibSlider.value, win.hslH, win.hslS, win.hslL,
        cgShHueSlider.value, cgShSatSlider.value, cgMidHueSlider.value, cgMidSatSlider.value,
        cgHiHueSlider.value, cgHiSatSlider.value, cgBalanceSlider.value, vignetteSlider.value
    ]
    onHistWatchChanged: win.refreshHistogram()

    // Export 파라미터(현상 전효과 + 지오메트리 + 해상도). CPU/GPU export 공용.
    function exportParams() {
        var o = {
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "texAmt": texSlider.value, "clarity": claritySlider.value, "dehaze": dehazeSlider.value,
            "saturation": satSlider.value, "vibrance": vibSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "sharpenAmt": sharpAmtSlider.value, "sharpenRadius": sharpRadiusSlider.value,
            "sharpenDetail": sharpDetailSlider.value, "sharpenMask": sharpMaskSlider.value,
            "lumaNR": lumaNrSlider.value, "colorNR": colorNrSlider.value,
            "vignette": vignetteSlider.value, "grainAmt": grainSlider.value, "grainSize": grainSizeSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0, "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value, "curves": curveEditor.allLuts(),
            "dateStamp": win.dateStamp, "stampText": stampField.text, "stampRot": controller.stampRot,
            "outEdge": win.exportEdges[resCombo.currentIndex], "lensCorrection": lensCheck.checked,
            "bitDepth": bitDepth16Check.checked ? 16 : 8,   // 16=TIFF/PNG 16bit(CPU 전용)
            // 지오메트리(현상 뒤 적용): 플립 -> 90° -> 스트레이튼(회전+채움줌) -> 종횡비 중앙크롭
            "flipH": flipHBtn.checked, "flipV": flipVBtn.checked,
            "quarterTurns": win.quarterTurns, "rotateAngle": rotAngleSlider.value,
            "cropX": win.cropX, "cropY": win.cropY, "cropW": win.cropW, "cropH": win.cropH,
            "geoV": geoVSlider.value, "geoH": geoHSlider.value, "geoScalePct": geoScaleSlider.value
        }
        // 하늘(로컬) 조정 병합 — CPU render_full 이 보관된 마스크(controller._sky_mask)와 함께 적용.
        var sk = win.skyEditParams()
        for (var k in sk) o[k] = sk[k]
        return o
    }

    // (앱 종료 시 편집 플러시 저장은 quitDialog 확인 후 onAccepted 에서 수행)

    // 탐색기 "좋아요만 보기" 필터 (L 키로 토글)
    property bool showLikedOnly: false
    Shortcut { sequence: "L"; onActivated: win.showLikedOnly = !win.showLikedOnly }
    // 필터 적용된 표시 목록: 좋아요만 보기면 폴더(탐색용) + 좋아요된 RAF 만.
    //  - controller.fileList(1회만 마샬링)·likeRevision·showLikedOnly 변경 시 자동 재평가
    property var explorerFiles: {
        controller.likeRevision               // 좋아요 토글 시 재평가용 의존
        var files = controller.fileList        // folderChanged 시 재평가 + 1회만 읽기
        if (!win.showLikedOnly)
            return files
        var out = []
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (it.isDir || controller.isLiked(it.path))   // 폴더는 항상 표시
                out.push(it)
        }
        return out
    }

    // Export 해상도 프리셋(긴 변 px, 0=원본). resCombo 모델 순서와 일치.
    readonly property var exportEdges: [0, 4096, 3840, 2560, 2048, 1920, 1280]

    // 콤보 인덱스 -> luts/<key>.cube 파일명. 0(identity)=필름시뮬 미적용.
    // controller.filmSims(=luts/*.cube 존재하는 것만)에서 파생 → 흑백 등 .cube 넣으면 자동 노출.
    readonly property var simKeys: {
        var k = []; var sims = controller.filmSims
        for (var i = 0; i < sims.length; i++) k.push(sims[i].key)
        return k
    }
    readonly property var simLabels: {
        var l = []; var sims = controller.filmSims
        for (var i = 0; i < sims.length; i++) l.push(sims[i].label)
        return l
    }

    function planckXY(T) {
        var x
        if (T < 4000) x = -0.2661239e9/(T*T*T) - 0.2343589e6/(T*T) + 0.8776956e3/T + 0.179910
        else          x = -3.0258469e9/(T*T*T) + 2.1070379e6/(T*T) + 0.2226347e3/T + 0.240390
        var y
        if (T < 2222)      y = -1.1063814*x*x*x - 1.34811020*x*x + 2.18555832*x - 0.20219683
        else if (T < 4000) y = -0.9549476*x*x*x - 1.37418593*x*x + 2.09137015*x - 0.16748867
        else               y =  3.0817580*x*x*x - 5.87338670*x*x + 3.75112997*x - 0.37001483
        return [x, y]
    }
    function planckCam(T) {
        var xy = planckXY(T)
        var X = xy[0]/xy[1], Y = 1.0, Z = (1.0 - xy[0] - xy[1])/xy[1]
        var m = controller.camMatrix
        return [Math.max(m[0]*X+m[1]*Y+m[2]*Z, 1e-6),
                Math.max(m[3]*X+m[4]*Y+m[5]*Z, 1e-6),
                Math.max(m[6]*X+m[7]*Y+m[8]*Z, 1e-6)]
    }
    function userWb(K, t) {           // wb.py compute_user_wb 와 동일
        var pr = planckCam(wbTRef), pc = planckCam(K), ref = controller.daylightRef
        var m = [ref[0]*pr[0]/pc[0], ref[1]*pr[1]/pc[1], ref[2]*pr[2]/pc[2]]
        m[0] /= m[1]; m[2] /= m[1]; m[1] = 1.0
        m[1] *= (1.0 - 0.3 * t)
        return m
    }
    function wbPreview(targetK, targetT) {   // baked->target 상대 게인
        var m = controller.camMatrix
        if (!m || m.length < 9) return Qt.vector3d(1, 1, 1)
        var t = userWb(targetK, targetT)
        var b = userWb(controller.bakedKelvin, controller.bakedTint)
        var g = [t[0]/b[0], t[1]/b[1], t[2]/b[2]]
        g[0] /= g[1]; g[2] /= g[1]; g[1] = 1.0    // green 정규화(노출 보존)
        return Qt.vector3d(g[0], g[1], g[2])
    }

    // 카메라 네이티브 -> 선형 sRGB 매트릭스(행우선 9개). 로드 전엔 identity.
    readonly property var camM: (controller.camToSrgb && controller.camToSrgb.length >= 9)
                                ? controller.camToSrgb : [1,0,0, 0,1,0, 0,0,1]
    // dispSrc(블러 base + 원본 비교)용 as-shot WB 상대게인(TREF 대비).
    // ⚠️as-shot tint 도 포함해야 함 — pipe 의 기본 WB(tempSlider=asShotKelvin,
    //   tintSlider=asShotTint)와 일치(편집 없을 때 원본=편집본). off-locus 광원(tint≠0)에서
    //   tint=0 으로 두면 색끼 차이 발생.
    readonly property vector3d asShotRelGain: win.wbPreview(controller.asShotKelvin, controller.asShotTint)

    // 슬라이더 더블클릭 리셋: press 중에는 Slider 가 value 를 커서 위치로 덮어쓰므로
    // press 시점엔 '더블 여부'만 판정하고, 실제 리셋은 release 때 수행한다(아래 슬라이더들).
    // 두 번째 press 가 400ms 안이면 true.
    function isDblPress(slider) {
        var now = Date.now()
        var dbl = (now - slider._lastPressMs < 400)
        slider._lastPressMs = dbl ? 0 : now
        return dbl
    }

    // 비-드래그(키보드) WB 변경 커밋용 디바운스.
    Timer {
        id: wbTimer
        interval: 150
        onTriggered: controller.setWb(tempSlider.value, tintSlider.value)
    }

    // 톤커브 배경 히스토그램 재계산(스로틀). 드래그 중 주기적 갱신(메인 스레드 부담 완화).
    Timer {
        id: histTimer
        interval: 130
        onTriggered: controller.updateHistogram(win.curParams())
    }
    // 스로틀: 실행 중이 아니면 시작 -> 연속 드래그 중에도 interval 마다 갱신(디바운스와 달리 멈춤 없음).
    function refreshHistogram() { if (!histTimer.running) histTimer.start() }

    function curParams() {
        return {
            "exposure": expSlider.value, "contrast": conSlider.value,
            "highlights": hiSlider.value, "shadows": shSlider.value,
            "whites": whSlider.value, "blacks": blSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0,
            "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value,
            "curves": curveEditor.allLuts(),
            // 라이트룸식 전체 반영: 색 단계 + 비네팅(그레인 제외)
            "saturation": satSlider.value, "vibrance": vibSlider.value,
            "hslH": win.hslH, "hslS": win.hslS, "hslL": win.hslL,
            "cgShadowHue": cgShHueSlider.value, "cgShadowSat": cgShSatSlider.value,
            "cgMidHue": cgMidHueSlider.value, "cgMidSat": cgMidSatSlider.value,
            "cgHighHue": cgHiHueSlider.value, "cgHighSat": cgHiSatSlider.value,
            "cgBalance": cgBalanceSlider.value,
            "vignette": vignetteSlider.value
        }
    }

    // 새 파일 로드 시 추정된 as-shot 색온도로 Temp 슬라이더 초기화.
    Connections {
        target: controller
        function onAsShotKelvinChanged() {
            // 저장된 편집이 있는 파일은 복원될 WB 를 유지(as-shot 으로 덮어쓰지 않음).
            if (win._hasSavedEdits()) return
            // _applying 가드: as-shot 으로 슬라이더를 맞추는 것은 '편집'이 아니므로 자동저장
            // 예약(editSaveWatch→scheduleSave)·WB 재디코딩(wbTimer)을 억제 → 새 사진에 불필요한
            // 사이드카(주황 배지)가 생기지 않게 한다.
            win._applying = true
            tempSlider.value = controller.asShotKelvin
            tintSlider.value = controller.asShotTint   // off-locus(불빛 등) as-shot tint 반영
            win._applying = false
        }
        // 로드/WB 커밋(재디코딩)으로 프록시가 갱신되면 조절 반영 히스토그램 재계산.
        function onImageChanged() { win.refreshHistogram(); viewport.resetZoom() }
        // 이미지 전환 직전: 이전 파일(controller._ui_path)로 편집 플러시 저장.
        // ⚠️보류 중(editSaveTimer.running=미저장 변경 있음)일 때만 저장 — 그렇지 않으면 이미
        // 저장됐거나 reset 으로 삭제된 상태라, 무조건 저장하면 기본값 사이드카를 되살린다(주황 재발).
        function onFlushEdits() {
            if (editSaveTimer.running && controller.imagePath !== "")
                controller.saveEdits(win.editParams())
            editSaveTimer.stop()
        }
    }

    FolderDialog {
        id: folderDialog
        title: "Select Folder"
        onAccepted: controller.setFolder(selectedFolder)   // QUrl -> Python .toLocalFile()
    }

    // 종료 확인 대화상자 (앱 컨셉: 다크 + 필름 퍼포레이션 + 앰버 강조, 커스텀 스타일)
    Popup {
        id: quitDialog
        modal: true
        dim: true
        width: 380
        padding: 0
        anchors.centerIn: Overlay.overlay
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "#000000"; opacity: 0.55 }
        background: Rectangle {
            color: "#232325"; radius: 16
            border.color: "#3d3d40"; border.width: 1
        }

        function doQuit() {
            if (controller.imagePath !== "") controller.saveEdits(win.editParams())  // 편집 플러시 저장
            win.allowClose = true
            Qt.quit()
        }

        contentItem: ColumnLayout {
            spacing: 0

            // 상단 필름 퍼포레이션 스트립(앰버) — 대화상자 폭을 가득 채움(좌우 여백은 둥근 모서리 회피).
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }

            ColumnLayout {
                Layout.fillWidth: true
                // 위/아래 여백 동일(24) → 콘텐츠가 상·하 필름 스트립 사이 중앙에 위치(위쏠림 방지)
                Layout.margins: 24
                spacing: 12

                Label {
                    text: "Quit FILM RAWSTERY?"
                    color: "#f2f2f2"; font.pixelSize: 18; font.bold: true
                    Layout.alignment: Qt.AlignHCenter
                }
                Label {
                    text: "Your current edits are saved before exit."
                    color: "#9a9a9a"; font.pixelSize: 13
                    Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 8
                    spacing: 12

                    Rectangle {        // Cancel
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: cancelMA.containsMouse ? "#3a3a3d" : "#2e2e31"
                        border.color: "#55555a"; border.width: 1
                        Label { anchors.centerIn: parent; text: "Cancel"; color: "#e6e6e6"; font.pixelSize: 13 }
                        MouseArea {
                            id: cancelMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: quitDialog.close()
                        }
                    }
                    Rectangle {        // Quit (앰버 강조)
                        Layout.fillWidth: true; Layout.preferredWidth: 0
                        Layout.preferredHeight: 40; radius: 8
                        color: okMA.containsMouse ? "#f0b945" : "#E0A226"
                        Label { anchors.centerIn: parent; text: "Quit"; color: "#1a1a1a"; font.pixelSize: 13; font.bold: true }
                        MouseArea {
                            id: okMA; anchors.fill: parent; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: quitDialog.doQuit()
                        }
                    }
                }
            }

            // 하단 필름 퍼포레이션 스트립 — 상단과 대칭(필름 프레임)
            FilmStrip {
                Layout.fillWidth: true
                Layout.leftMargin: 16; Layout.rightMargin: 16
                Layout.preferredHeight: 26
            }
        }
    }

    // 프리뷰 모드 오버레이(탐색기에서 RAF 우클릭 → 메뉴 Preview 로 염). 메인 창 위를 꽉 덮음.
    PreviewWindow { id: previewWin }

    // 탐색기에서 우클릭한 파일을 프리뷰 창으로 연다.
    // 현재 폴더의 RAF(디렉터리 제외)만 경로 배열로 만들어 좌/우 네비 대상으로 넘긴다.
    function openPreview(path) {
        var files = win.explorerFiles       // 현재 보이는(필터 반영) 목록 기준으로 좌/우 이동
        var list = []
        var start = 0
        for (var i = 0; i < files.length; i++) {
            var it = files[i]
            if (!it.isDir) {
                if (it.path === path) start = list.length
                list.push(it.path)
            }
        }
        if (list.length > 0)
            previewWin.open(list, start)
    }

    // 폴더가 바뀌면 좌측 리스트 선택 하이라이트 초기화(잔상 방지).
    Connections {
        target: controller
        function onFolderChanged() { fileListView.currentIndex = -1 }
    }

    FileDialog {
        id: saveDialog
        title: "Export (Full Resolution)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["PNG (*.png)", "JPEG (*.jpg)", "TIFF (*.tif)"]
        defaultSuffix: "png"
        // 렌더 모드: 0=CPU(render_full), 1=GPU(프리뷰 셰이더로 풀해상도 렌더 → 프리뷰=Export)
        onAccepted: {
            var p = win.exportParams()
            // GPU grab 은 8bit 라 16bit 선택 시 무조건 CPU 경로 사용.
            if (renderModeCombo.currentIndex === 1 && !bitDepth16Check.checked) {
                gpuExportLoader.active = true     // 풀해상도 셰이더 체인 인스턴스화(grab 대기)
                controller.exportImageGpu(selectedFile, p)
            } else {
                controller.exportImage(selectedFile, p)
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---------- 좌측: File Explorer ----------
        Rectangle {
            visible: win.showExplorer      // B 키 / 토글 버튼으로 show/hide
            Layout.preferredWidth: 260
            Layout.fillHeight: true
            color: "#2b2b2b"

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 8
                spacing: 6

                // 헤더: 상위 폴더 / 폴더 선택
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        text: "⬆"
                        Layout.preferredWidth: 30
                        ToolTip.visible: hovered
                        ToolTip.text: "Parent folder"
                        onClicked: controller.goUp()
                    }
                    Button {
                        id: folderBtn
                        text: "Folder…"
                        Layout.fillWidth: true
                        onClicked: folderDialog.open()
                    }
                    // "좋아요만 보기" 토글 — Canvas 하트(활성=채움/적색, 비활성=외곽선/회색)
                    Rectangle {
                        id: likeFilterBtn
                        Layout.preferredWidth: 36
                        Layout.preferredHeight: folderBtn.height   // 옆 버튼 높이에 맞춤
                        Layout.alignment: Qt.AlignVCenter
                        radius: 5
                        color: win.showLikedOnly ? "#3a2a2e"
                             : (lfHover.hovered ? "#3a3f4b" : "transparent")
                        border.color: win.showLikedOnly ? "#ff6b6b" : "#555555"
                        border.width: 1

                        ToolTip.visible: lfHover.hovered
                        ToolTip.text: "Show liked only (L)"

                        // 팝업 패널과 동일하게 ♥(채움)/♡(빈) 글리프로 활성/비활성 표시
                        Text {
                            anchors.centerIn: parent
                            text: win.showLikedOnly ? "♥" : "♡"
                            color: win.showLikedOnly ? "#ff6b6b" : "#cfcfcf"
                            font.pixelSize: 19
                        }
                        HoverHandler { id: lfHover }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.showLikedOnly = !win.showLikedOnly
                        }
                    }
                }

                // 현재 폴더 경로
                Label {
                    Layout.fillWidth: true
                    text: controller.currentFolder || "Select a folder"
                    color: "#9a9a9a"
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // 파일/폴더 리스트 (ListView = 화면에 보이는 항목만 썸네일 요청 → 지연 로딩)
                ListView {
                    id: fileListView
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 2
                    cacheBuffer: 400
                    model: win.explorerFiles      // "좋아요만 보기" 필터 반영
                    currentIndex: -1
                    boundsBehavior: Flickable.StopAtBounds
                    enabled: !controller.busy      // 로드 진행 중엔 사진 변경 차단
                    opacity: controller.busy ? 0.5 : 1.0   // 비활성 시각 표시

                    B.ScrollBar.vertical: B.ScrollBar {
                        id: fileVbar
                        width: 10
                        policy: B.ScrollBar.AsNeeded
                        contentItem: Rectangle {
                            implicitWidth: 6
                            radius: 3
                            color: fileVbar.pressed ? "#cfcfcf" : "#9a9a9a"
                        }
                        background: Rectangle { radius: 3; color: "#3a3a3a" }
                    }

                    delegate: Item {
                        id: row
                        required property var modelData
                        required property int index
                        width: ListView.view ? ListView.view.width : 0
                        height: modelData.isDir ? 28 : 64
                        readonly property bool isLoaded:
                            !modelData.isDir && modelData.path === controller.imagePath

                        Rectangle {
                            anchors.fill: parent
                            anchors.rightMargin: 12      // 스크롤바 영역 비움
                            radius: 4
                            color: row.isLoaded ? "#2d4a6b"
                                 : (fileListView.currentIndex === row.index ? "#3a3f4b"
                                                                            : "transparent")
                            border.color: row.isLoaded ? "#8ab4f8" : "transparent"
                            border.width: row.isLoaded ? 1 : 0

                            RowLayout {
                                anchors.fill: parent
                                anchors.margins: 4
                                spacing: 8

                                // 썸네일(파일) 또는 폴더 아이콘
                                Item {
                                    Layout.preferredWidth: modelData.isDir ? 20 : 84
                                    Layout.preferredHeight: modelData.isDir ? 20 : 56
                                    Layout.alignment: Qt.AlignVCenter

                                    Text {
                                        visible: modelData.isDir
                                        anchors.centerIn: parent
                                        text: "📁"
                                        font.pixelSize: 16
                                    }
                                    Rectangle {     // 로딩중/실패 placeholder
                                        visible: !modelData.isDir && thumbImg.status !== Image.Ready
                                        anchors.fill: parent
                                        color: "#1e1e1e"
                                        radius: 2
                                    }
                                    Image {
                                        id: thumbImg
                                        visible: !modelData.isDir
                                        anchors.fill: parent
                                        fillMode: Image.PreserveAspectFit
                                        asynchronous: true
                                        cache: true
                                        sourceSize.width: 96    // → requestImage requested_size
                                        source: modelData.isDir ? ""
                                                : "image://thumb/" + encodeURIComponent(modelData.path)
                                    }
                                    // 좋아요(셀렉트) 하트 배지 — likeRevision 참조로 토글/폴더변경 시 갱신
                                    Text {
                                        anchors.right: parent.right
                                        anchors.bottom: parent.bottom
                                        anchors.margins: 1
                                        text: "♥"
                                        color: "#ff6b6b"
                                        style: Text.Outline
                                        styleColor: "#000000"
                                        font.pixelSize: 14
                                        visible: {
                                            controller.likeRevision
                                            return !modelData.isDir
                                                   && controller.isLiked(modelData.path)
                                        }
                                    }
                                }

                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.name
                                    // 편집 사이드카(.filmrawsteryedits/<name>.json)가 있으면 파일명을 앰버로
                                    // 표시(저장된 편집 표시). editsRevision 참조로 저장/폴더 변경 시 갱신.
                                    color: {
                                        controller.editsRevision
                                        return (!modelData.isDir && controller.hasEdits(modelData.path))
                                               ? "#E0A226" : "#e6e6e6"
                                    }
                                    font.pixelSize: 12
                                    elide: Text.ElideMiddle
                                    maximumLineCount: 2
                                    wrapMode: Text.WrapAnywhere
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }

                        // 우클릭 컨텍스트 메뉴(파일 전용): Preview 항목
                        B.Menu {
                            id: ctxMenu
                            B.MenuItem {
                                text: "Preview"
                                onTriggered: win.openPreview(row.modelData.path)
                                contentItem: Text {
                                    text: parent.text
                                    color: "#e6e6e6"
                                    font.pixelSize: 12
                                    verticalAlignment: Text.AlignVCenter
                                    leftPadding: 8
                                }
                                background: Rectangle {
                                    implicitWidth: 140
                                    implicitHeight: 28
                                    color: parent.highlighted ? "#3a4a6b" : "transparent"
                                }
                            }
                            background: Rectangle {
                                implicitWidth: 140
                                color: "#2b2b2b"
                                border.color: "#444"
                                border.width: 1
                                radius: 4
                            }
                        }

                        MouseArea {
                            anchors.fill: parent
                            acceptedButtons: Qt.LeftButton | Qt.RightButton
                            onClicked: (mouse) => {
                                if (mouse.button === Qt.RightButton) {
                                    fileListView.currentIndex = row.index
                                    if (!row.modelData.isDir)
                                        ctxMenu.popup()             // 우클릭 = 컨텍스트 메뉴
                                } else {
                                    fileListView.currentIndex = row.index     // 좌클릭 = 선택만
                                }
                            }
                            onDoubleClicked: {
                                if (row.modelData.isDir)
                                    controller.setFolderPath(row.modelData.path)
                                else
                                    controller.loadPath(row.modelData.path)    // 로컬경로 디코딩 로드
                            }
                        }
                    }
                }

                // 푸터: GitHub 저장소 링크 (클릭 시 외부 브라우저로 열기)
                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 24
                    color: "transparent"
                    ToolTip.visible: ghHover.hovered
                    ToolTip.text: "Open GitHub repository — lim8701/FilmRawstery"
                    Text {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        text: "GitHub ↗"
                        color: ghHover.hovered ? "#8ab4f8" : "#8a8a8a"
                        font.pixelSize: 12
                        font.underline: ghHover.hovered
                    }
                    HoverHandler { id: ghHover }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: Qt.openUrlExternally("https://github.com/lim8701/FilmRawstery")
                    }
                }
            }
        }

        // ---------- 탐색기 show/hide 핸들 (세로로 꽉 찬 얇은 바) ----------
        // 패널이 숨겨져도 항상 보여 다시 열 수 있게 한다.
        Rectangle {
            Layout.preferredWidth: 12
            Layout.fillHeight: true
            color: handleArea.containsMouse ? "#3a3f4b" : "#222"

            Text {
                anchors.centerIn: parent
                text: win.showExplorer ? "‹" : "›"
                color: "#cfcfcf"
                font.pixelSize: 16
            }

            MouseArea {
                id: handleArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: win.showExplorer = !win.showExplorer
            }

            ToolTip.visible: handleArea.containsMouse
            ToolTip.delay: 1500        // 호버 즉시 말고 1.5초 뒤 표시
            ToolTip.text: (win.showExplorer ? "Hide explorer" : "Show explorer") + " (B)"
        }

        // ---------- 이미지 영역 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#1e1e1e"

            // 날짜 입력칸 포커스 중 이미지 영역을 탭하면 포커스 해제 → 단축키 복귀.
            // passive grab 이라 크롭/팬 등 기존 드래그 조작은 가로채지 않음.
            TapHandler {
                enabled: stampField.activeFocus
                onTapped: stampField.focus = false
            }

            // 텍스처 소스 (화면에는 직접 안 보임, ShaderEffect 입력으로만 사용)
            Image {
                id: srcImage
                visible: false
                cache: false
                source: controller.imageUrl
            }

            // LUT 아틀라스 텍스처. nearest 필터를 위해 smooth:false 필수.
            Image {
                id: lutImage
                visible: false
                cache: false
                smooth: false
                source: "image://lut/" + win.simKeys[simCombo.currentIndex]
            }

            // 디스플레이 색관리 LUT 아틀라스(sRGB→모니터). 수동 트라이리니어라 smooth:false 필수.
            Image {
                id: cmLutImage
                visible: false
                cache: false
                smooth: false
                source: controller.cmLutUrl
            }

            // 톤 커브 1D LUT 텍스처 (256x1). 보간 위해 smooth:true.
            Image {
                id: curveImage
                visible: false
                cache: false
                smooth: true
                source: controller.curveUrl
            }

            // 날짜 스탬프 오버레이 텍스처(프록시 RGBA). 셰이더가 가산 합성.
            Image {
                id: stampImage
                visible: false
                cache: false
                smooth: true
                source: controller.stampUrl
            }

            // 하늘 마스크 텍스처(프록시 크기 단일채널). 셰이더가 하늘 로컬조정에 게이팅.
            Image {
                id: skyMaskImage
                visible: false
                cache: false
                smooth: true
                source: controller.skyMaskUrl
            }

            // ── GPU export: 풀해상도를 프리뷰와 **동일한 adjust.frag** 로 렌더(프리뷰=Export) ──
            //   온디맨드(렌더=GPU 일 때만 active). src 만 풀해상도, 블러 텍스처는 프록시 것 재사용
            //   (로컬대비/톤마스크 성격을 프리뷰와 동일하게). uniform 바인딩은 pipe 와 반드시 동일.
            Loader {
                id: gpuExportLoader
                active: false
                sourceComponent: Component { Item {
                    property bool grabPending: false
                    function doGrab() {
                        pipeFull.grabToImage(function(res) {
                            controller.saveGrab(res.image)
                            Qt.callLater(function() { gpuExportLoader.active = false })
                        }, Qt.size(pipeFull.width, pipeFull.height))
                    }
                    Image {
                        id: srcFull; visible: false; cache: false; smooth: true
                        source: controller.fullUrl
                        onStatusChanged: if (status === Image.Ready && grabPending) {
                            grabPending = false; doGrab()
                        }
                    }
                    Connections {
                        target: controller
                        function onFullReady() {
                            if (srcFull.status === Image.Ready) doGrab()
                            else grabPending = true
                        }
                    }
                    ShaderEffect {
                        id: pipeFull
                        width: srcFull.implicitWidth > 0 ? srcFull.implicitWidth : 1
                        height: srcFull.implicitHeight > 0 ? srcFull.implicitHeight : 1
                        visible: false
                        // ⚠️아래 uniform 바인딩은 pipe 와 동일하게 유지해야 함(프리뷰=Export).
                        property variant src: srcFull
                        property variant dispSrc: dispSrcTex
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property variant sharpBlur: sharpBlurTex
                        property variant stampTex: stampImage
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        property real contrast: conSlider.value
                        property real highlights: hiSlider.value
                        property real shadows: shSlider.value
                        property real whites: whSlider.value
                        property real blacks: blSlider.value
                        property real texAmt: texSlider.value
                        property real clarity: claritySlider.value
                        property real dehaze: dehazeSlider.value
                        property real saturation: satSlider.value
                        property real vibrance: vibSlider.value
                        property vector4d hslHa: Qt.vector4d(win.hslH[0], win.hslH[1], win.hslH[2], win.hslH[3])
                        property vector4d hslHb: Qt.vector4d(win.hslH[4], win.hslH[5], win.hslH[6], win.hslH[7])
                        property vector4d hslSa: Qt.vector4d(win.hslS[0], win.hslS[1], win.hslS[2], win.hslS[3])
                        property vector4d hslSb: Qt.vector4d(win.hslS[4], win.hslS[5], win.hslS[6], win.hslS[7])
                        property vector4d hslLa: Qt.vector4d(win.hslL[0], win.hslL[1], win.hslL[2], win.hslL[3])
                        property vector4d hslLb: Qt.vector4d(win.hslL[4], win.hslL[5], win.hslL[6], win.hslL[7])
                        property real sharpenAmt: sharpAmtSlider.value
                        property real sharpenDetail: sharpDetailSlider.value
                        property real sharpenMask: sharpMaskSlider.value
                        property real texelW: 1.0 / Math.max(1, width)
                        property real texelH: 1.0 / Math.max(1, height)
                        property real vignette: vignetteSlider.value
                        property real grainAmt: grainSlider.value
                        property real grainSize: grainSizeSlider.value
                        property real grainAspect: width / Math.max(1, height)
                        property real clipWarn: 0.0   // export 는 클리핑 오버레이 미적용
                        property real displayCM: 0.0  // export 는 디스플레이 색관리 미적용(표준 sRGB)
                        property variant cmLut: cmLutImage
                        property real cmLutSize: controller.cmLutN
                        // 컬러 그레이딩 — 프리뷰(pipe)와 동일 바인딩(export 일치).
                        property real cgHueSh: cgShHueSlider.value / 360.0
                        property real cgSatSh: cgShSatSlider.value
                        property real cgHueMid: cgMidHueSlider.value / 360.0
                        property real cgSatMid: cgMidSatSlider.value
                        property real cgHueHi: cgHiHueSlider.value / 360.0
                        property real cgSatHi: cgHiSatSlider.value
                        property real cgBalance: cgBalanceSlider.value
                        property real lumaNR: lumaNrSlider.value
                        property real colorNR: colorNrSlider.value
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN
                        property real lutStrength: simStrengthSlider.value
                        property int lutEnabled: simCombo.currentIndex === 0 ? 0 : 1
                        // 하늘(로컬) 조정 — 프리뷰(pipe)와 동일 바인딩. 오버레이는 export 미적용(0).
                        property variant skyMask: skyMaskImage
                        property real skyExp: skyExpSlider.value
                        property real skyTemp: skyTempSlider.value
                        property real skyTint: skyTintSlider.value
                        property real skySat: skySatSlider.value
                        property real skyHi: skyHiSlider.value
                        property real skyShadows: skyShadowsSlider.value
                        property real skyTexture: skyTextureSlider.value
                        property real skyClarity: skyClaritySlider.value
                        property real skyDehaze: skyDehazeSlider.value
                        property real skyInvert: skyInvertCheck.checked ? 1.0 : 0.0
                        property real skyHasMask: controller.hasSkyMask ? 1.0 : 0.0
                        property real skyShowMask: 0.0
                        // 현상 계수(coeffs.py 단일 진실원) uniform 주입 — pipeline.py 와 값 공유.
                        property real dehazeKLocal: controller.adjustCoeffs["dehazeKLocal"]
                        property real dehazeKContrast: controller.adjustCoeffs["dehazeKContrast"]
                        property real dehazeKVeil: controller.adjustCoeffs["dehazeKVeil"]
                        property real dehazeKSat: controller.adjustCoeffs["dehazeKSat"]
                        property real clarityK: controller.adjustCoeffs["clarityK"]
                        property real textureK: controller.adjustCoeffs["textureK"]
                        property real skyTempK: controller.adjustCoeffs["skyTempK"]
                        property real skyTintK: controller.adjustCoeffs["skyTintK"]
                        property real toneHiShK: controller.adjustCoeffs["toneHiShK"]
                        property real toneWhBlK: controller.adjustCoeffs["toneWhBlK"]
                        property real vignetteK: controller.adjustCoeffs["vignetteK"]
                        property real grainK: controller.adjustCoeffs["grainK"]
                        property real sharpenK: controller.adjustCoeffs["sharpenK"]
                        property real hslHueDegK: controller.adjustCoeffs["hslHueDegK"]
                        property real hslLumK: controller.adjustCoeffs["hslLumK"]
                        property real colorGradeK: controller.adjustCoeffs["colorGradeK"]
                        fragmentShader: "shaders/adjust.frag.qsb"
                    }
                }}
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // 상단: 열린 파일 경로\파일명 표시줄
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 26
                    color: "#252525"

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        color: "#cfcfcf"
                        font.pixelSize: 12
                        elide: Text.ElideMiddle
                        width: parent.width - 20
                        text: controller.imagePath !== ""
                              ? controller.imagePath
                              : "No file open"
                    }
                }

                Item {
                    id: viewport
                    Layout.fillWidth: true
                    Layout.fillHeight: true

                    property real margin: 12
                    property real availW: width - margin * 2
                    property real availH: height - margin * 2
                    // 처리 해상도 = 프록시 native (모니터 해상도와 무관하게 GPU 부하 고정)
                    property real procW: srcImage.implicitWidth > 0 ? srcImage.implicitWidth : 1
                    property real procH: srcImage.implicitHeight > 0 ? srcImage.implicitHeight : 1
                    property real claW: Math.max(1, Math.round(procW / 4))   // 클래리티 블러 다운샘플
                    property real claH: Math.max(1, Math.round(procH / 4))

                    // === 회전/크롭(지오메트리) 미리보기 기하 (export numpy 와 동일 정의) ===
                    // 크롭 패널(activePanel===1)에서는 전체 캔버스+편집 박스를, 그 외엔 크롭 결과를 표시.
                    property bool cropEdit: win.activePanel === 1
                    property bool geoOdd: (win.quarterTurns % 2) !== 0
                    property real caW: geoOdd ? procH : procW     // 90° 회전 후 캔버스 크기
                    property real caH: geoOdd ? procW : procH
                    property real cA: caW / Math.max(1, caH)       // 캔버스 비율(가로/세로)
                    // 크롭 결과 비율(가로/세로)
                    property real cropDispAspect: (win.cropW * caW) / Math.max(1e-4, win.cropH * caH)
                    // 스트레이튼(자유각) 채움 줌: 회전해도 빈 모서리가 안 생기게 캔버스를 채움.
                    property real straightenZoom: {
                        var t = Math.abs(rotAngleSlider.value) * Math.PI / 180.0
                        return Math.cos(t) + Math.max(cA, 1.0 / cA) * Math.sin(t)
                    }
                    // 캔버스 전체 표시 크기: 편집=캔버스 fit, 결과=크롭이 viewport 를 채우게 캔버스 확대
                    property real canvasDispW: cropEdit
                        ? Math.min(availW, availH * cA)
                        : Math.min(availW, availH * cropDispAspect) / Math.max(1e-4, win.cropW)
                    property real canvasDispH: canvasDispW / Math.max(1e-4, cA)
                    // 캔버스 px -> 화면 fit 스케일(스트레이튼 줌은 원근 앞에 별도 적용 → export 와 순서 일치)
                    property real fitScale: canvasDispW / Math.max(1, caW)
                    // 표시 클립 박스: 편집=캔버스 전체, 결과=크롭 영역
                    property real clipW: cropEdit ? canvasDispW : (canvasDispW * win.cropW)
                    property real clipH: cropEdit ? canvasDispH : (canvasDispH * win.cropH)

                    // === 1:1 확대 & 패닝(핀트 확인). 프록시(≤2560) 기준 1:1(proxy px:screen px). ===
                    clip: true                       // 확대 시 이미지가 패널을 침범하지 않게
                    property bool zoomed: false
                    property real panX: 0
                    property real panY: 0
                    property real zoomFactor: 1.0 / Math.max(1e-4, fitScale)
                    function clampPan() {
                        var mx = Math.max(0, (clipW * zoomFactor - width) / 2)
                        var my = Math.max(0, (clipH * zoomFactor - height) / 2)
                        panX = Math.max(-mx, Math.min(mx, panX))
                        panY = Math.max(-my, Math.min(my, panY))
                    }
                    function zoomToPoint(px, py) {     // 클릭점을 중앙으로 → 확대
                        panX = -(px - width / 2) * zoomFactor
                        panY = -(py - height / 2) * zoomFactor
                        zoomed = true; clampPan()
                    }
                    function resetZoom() { zoomed = false; panX = 0; panY = 0 }
                    onCropEditChanged: if (cropEdit) resetZoom()   // 크롭 패널 진입 시 확대 해제

                    // 원근(키스톤)+배율 호모그래피 (export pipeline._persp_homography 와 동일 수식).
                    // GEO_PERSP_K=0.35 강도 일치 필수. 중심 기준, 소스(procW/procH) 정규화.
                    property matrix4x4 perspMat: {
                        var cx = procW / 2, cy = procH / 2
                        var s = geoScaleSlider.value / 100.0
                        var kxn = (geoHSlider.value / 100.0) * 0.35
                        var kyn = (geoVSlider.value / 100.0) * 0.35
                        var kx = kxn / Math.max(1, procW / 2)
                        var ky = kyn / Math.max(1, procH / 2)
                        var w0 = 1.0 - kx * cx - ky * cy
                        var h00 = s + cx * kx, h01 = cx * ky, h02 = cx * w0 - s * cx
                        var h10 = cy * kx, h11 = s + cy * ky, h12 = cy * w0 - s * cy
                        return Qt.matrix4x4(h00, h01, 0, h02,
                                            h10, h11, 0, h12,
                                            0, 0, 1, 0,
                                            kx, ky, 0, w0)
                    }

                    // --- dispSrc: 카메라네이티브 src -> display sRGB(as-shot WB) 변환 ---
                    // 블러 체인과 메인 셰이더의 로컬대비 base. srcImage·asShot 에만 의존.
                    ShaderEffect {
                        id: dispPre; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: srcImage
                        property real relR: win.asShotRelGain.x
                        property real relG: win.asShotRelGain.y
                        property real relB: win.asShotRelGain.z
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        fragmentShader: "shaders/convert.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: dispSrcTex; sourceItem: dispPre; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }

                    // Compare original 모드용: 무편집 display sRGB(dispPre)에 디스플레이 색관리만 적용.
                    // pipe 와 동일한 CM 을 거쳐 'before' 도 광색역 패널에서 정확히 표시(프리뷰 일관).
                    ShaderEffect {
                        id: comparePipe; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property variant cmLut: cmLutImage
                        property real displayCM: (win.displayCM && controller.hasDisplayCM) ? 1.0 : 0.0
                        property real cmLutSize: controller.cmLutN
                        fragmentShader: "shaders/displaycm.frag.qsb"
                    }

                    // --- 로컬대비용 가우시안 블러 (dispSrc 에만 의존 -> 로드 시 1회 계산) ---
                    // 텍스처: 작은 반경, 풀 프록시 해상도
                    ShaderEffect {
                        id: texBlurH; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(1.25 / viewport.procW, 0)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: texBlurHSrc; sourceItem: texBlurH; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: texBlurV; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: texBlurHSrc
                        property vector2d dir: Qt.vector2d(0, 1.25 / viewport.procH)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: texBlurTex; sourceItem: texBlurV; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }
                    // 클래리티: 큰 반경, 1/4 다운샘플
                    ShaderEffect {
                        id: claBlurH; visible: false
                        width: viewport.claW; height: viewport.claH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(1.5 / viewport.claW, 0)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: claBlurHSrc; sourceItem: claBlurH; visible: false
                        textureSize: Qt.size(viewport.claW, viewport.claH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: claBlurV; visible: false
                        width: viewport.claW; height: viewport.claH
                        property variant src: claBlurHSrc
                        property vector2d dir: Qt.vector2d(0, 1.5 / viewport.claH)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: claBlurTex; sourceItem: claBlurV; visible: false
                        textureSize: Qt.size(viewport.claW, viewport.claH)
                        hideSource: true; live: true; smooth: true
                    }
                    // 샤프닝: 가변 반경 블러(Radius 슬라이더에 dir 바인딩 → 반경 변경 시만 재계산)
                    ShaderEffect {
                        id: sharpBlurH; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: dispSrcTex
                        property vector2d dir: Qt.vector2d(sharpRadiusSlider.value / viewport.procW, 0)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: sharpBlurHSrc; sourceItem: sharpBlurH; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true
                    }
                    ShaderEffect {
                        id: sharpBlurV; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: sharpBlurHSrc
                        property vector2d dir: Qt.vector2d(0, sharpRadiusSlider.value / viewport.procH)
                        fragmentShader: "shaders/blur.frag.qsb"
                    }
                    ShaderEffectSource {
                        id: sharpBlurTex; sourceItem: sharpBlurV; visible: false
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        hideSource: true; live: true; smooth: true
                    }

                    // 파이프라인 셰이더: 프록시 해상도에서만 렌더(직접 표시 안 함)
                    ShaderEffect {
                        id: pipe
                        width: viewport.procW
                        height: viewport.procH
                        visible: false

                        // 셰이더 uniform 과 이름이 일치해야 함
                        property variant src: srcImage
                        property variant dispSrc: dispSrcTex
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property variant sharpBlur: sharpBlurTex
                        property variant stampTex: stampImage
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: 0.0   // 스탬프는 셰이더(원본 코너)가 아니라 cropClip 위 stampOverlay 가 최종 프레임 기준으로 그림
                        property real stampStrength: 0.92
                        property real exposure: expSlider.value
                        property real contrast: conSlider.value
                        property real highlights: hiSlider.value
                        property real shadows: shSlider.value
                        property real whites: whSlider.value
                        property real blacks: blSlider.value
                        property real texAmt: texSlider.value
                        property real clarity: claritySlider.value
                        property real dehaze: dehazeSlider.value
                        property real saturation: satSlider.value
                        property real vibrance: vibSlider.value
                        // HSL 컬러 믹서 (8색상대 → vec4 ×2씩: a=0..3, b=4..7)
                        property vector4d hslHa: Qt.vector4d(win.hslH[0], win.hslH[1], win.hslH[2], win.hslH[3])
                        property vector4d hslHb: Qt.vector4d(win.hslH[4], win.hslH[5], win.hslH[6], win.hslH[7])
                        property vector4d hslSa: Qt.vector4d(win.hslS[0], win.hslS[1], win.hslS[2], win.hslS[3])
                        property vector4d hslSb: Qt.vector4d(win.hslS[4], win.hslS[5], win.hslS[6], win.hslS[7])
                        property vector4d hslLa: Qt.vector4d(win.hslL[0], win.hslL[1], win.hslL[2], win.hslL[3])
                        property vector4d hslLb: Qt.vector4d(win.hslL[4], win.hslL[5], win.hslL[6], win.hslL[7])
                        property real sharpenAmt: sharpAmtSlider.value
                        property real sharpenDetail: sharpDetailSlider.value
                        property real sharpenMask: sharpMaskSlider.value
                        property real texelW: 1.0 / Math.max(1, viewport.procW)
                        property real texelH: 1.0 / Math.max(1, viewport.procH)
                        property real vignette: vignetteSlider.value
                        property real grainAmt: grainSlider.value
                        property real grainSize: grainSizeSlider.value
                        property real grainAspect: viewport.procW / Math.max(1, viewport.procH)
                        property real clipWarn: win.clipWarn ? 1.0 : 0.0   // 클리핑 경고 오버레이(프리뷰 전용)
                        // 디스플레이 색관리(프리뷰 전용): 토글 ON + 유효 CM LUT 있을 때만.
                        property real displayCM: (win.displayCM && controller.hasDisplayCM) ? 1.0 : 0.0
                        property variant cmLut: cmLutImage
                        property real cmLutSize: controller.cmLutN
                        // 컬러 그레이딩(스플릿 토닝): hue 슬라이더(도) → 0..1 정규화.
                        property real cgHueSh: cgShHueSlider.value / 360.0
                        property real cgSatSh: cgShSatSlider.value
                        property real cgHueMid: cgMidHueSlider.value / 360.0
                        property real cgSatMid: cgMidSatSlider.value
                        property real cgHueHi: cgHiHueSlider.value / 360.0
                        property real cgSatHi: cgHiSatSlider.value
                        property real cgBalance: cgBalanceSlider.value
                        property real lumaNR: lumaNrSlider.value
                        property real colorNR: colorNrSlider.value
                        // WB 게인: TREF 베이크 대비 상대게인(카메라공간). 재디코딩 없이 실시간.
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN             // context property (LUT 크기 N)
                        property real lutStrength: simStrengthSlider.value
                        property int lutEnabled: simCombo.currentIndex === 0 ? 0 : 1
                        // 하늘(로컬) 조정 — ML 세그 마스크에만 적용. showSkyMask=선택영역 시각화(프리뷰 전용).
                        property variant skyMask: skyMaskImage
                        property real skyExp: skyExpSlider.value
                        property real skyTemp: skyTempSlider.value
                        property real skyTint: skyTintSlider.value
                        property real skySat: skySatSlider.value
                        property real skyHi: skyHiSlider.value
                        property real skyShadows: skyShadowsSlider.value
                        property real skyTexture: skyTextureSlider.value
                        property real skyClarity: skyClaritySlider.value
                        property real skyDehaze: skyDehazeSlider.value
                        property real skyInvert: skyInvertCheck.checked ? 1.0 : 0.0
                        property real skyHasMask: controller.hasSkyMask ? 1.0 : 0.0
                        property real skyShowMask: win.showSkyMask ? 1.0 : 0.0
                        // 현상 계수(coeffs.py 단일 진실원) uniform 주입 — pipeline.py 와 값 공유.
                        property real dehazeKLocal: controller.adjustCoeffs["dehazeKLocal"]
                        property real dehazeKContrast: controller.adjustCoeffs["dehazeKContrast"]
                        property real dehazeKVeil: controller.adjustCoeffs["dehazeKVeil"]
                        property real dehazeKSat: controller.adjustCoeffs["dehazeKSat"]
                        property real clarityK: controller.adjustCoeffs["clarityK"]
                        property real textureK: controller.adjustCoeffs["textureK"]
                        property real skyTempK: controller.adjustCoeffs["skyTempK"]
                        property real skyTintK: controller.adjustCoeffs["skyTintK"]
                        property real toneHiShK: controller.adjustCoeffs["toneHiShK"]
                        property real toneWhBlK: controller.adjustCoeffs["toneWhBlK"]
                        property real vignetteK: controller.adjustCoeffs["vignetteK"]
                        property real grainK: controller.adjustCoeffs["grainK"]
                        property real sharpenK: controller.adjustCoeffs["sharpenK"]
                        property real hslHueDegK: controller.adjustCoeffs["hslHueDegK"]
                        property real hslLumK: controller.adjustCoeffs["hslLumK"]
                        property real colorGradeK: controller.adjustCoeffs["colorGradeK"]

                        fragmentShader: "shaders/adjust.frag.qsb"
                    }

                    // 고정 크기 FBO(프록시 해상도)에 렌더 -> 회전/크롭(지오메트리)을 뷰 변환으로
                    // 적용. cropClip 이 표시 영역(편집=캔버스 전체 / 결과=크롭)으로 잘라낸다.
                    // export numpy 와 동일 기하 순서: 플립 -> 90° -> 스트레이튼 -> 자유 사각 크롭.
                    Item {
                        id: cropClip
                        visible: srcImage.status === Image.Ready
                        anchors.centerIn: parent
                        width: viewport.clipW
                        height: viewport.clipH
                        clip: true
                        // 1:1 확대 & 패닝 — 중앙 기준 스케일 후 팬(translate). 핀트 확인용.
                        scale: viewport.zoomed ? viewport.zoomFactor : 1.0
                        transform: Translate {
                            x: viewport.zoomed ? viewport.panX : 0
                            y: viewport.zoomed ? viewport.panY : 0
                        }

                        // 캔버스 홀더: 편집모드=(0,0)으로 캔버스 전체가 cropClip 채움,
                        // 결과모드=크롭 영역의 좌상단이 cropClip 좌상단에 오도록 음수 오프셋.
                        Item {
                            id: canvasHolder
                            width: viewport.canvasDispW
                            height: viewport.canvasDispH
                            x: viewport.cropEdit ? 0 : -win.cropX * viewport.canvasDispW
                            y: viewport.cropEdit ? 0 : -win.cropY * viewport.canvasDispH
                            // 회전/원근으로 변환된 텍스처 가장자리 안티엘리어싱:
                            // 자식(transform 적용된 pipeView)을 멀티샘플 FBO 에 렌더.
                            layer.enabled: true
                            layer.smooth: true
                            layer.samples: 4
                            // 1:1 확대 시 FBO 를 프록시 native 해상도로 렌더(아니면 fit 해상도라 확대=흐릿).
                            // 평소엔 Qt.size(0,0)=아이템 크기(기존 동작 유지).
                            layer.textureSize: viewport.zoomed ? Qt.size(viewport.caW, viewport.caH)
                                                               : Qt.size(0, 0)

                            ShaderEffectSource {
                                id: pipeView
                                // 원본 비교 중에는 무편집 현상(dispPre)을 같은 변환/크롭으로 표시.
                                sourceItem: win.compareOn ? comparePipe : pipe
                                textureSize: Qt.size(viewport.procW, viewport.procH)
                                width: viewport.procW
                                height: viewport.procH
                                anchors.centerIn: parent
                                hideSource: true
                                smooth: true
                                live: true
                                // transform 리스트는 나열 순서대로 적용(앞=먼저=안쪽): 플립 -> 회전 -> 줌.
                                transform: [
                                    Scale {
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: flipHBtn.checked ? -1 : 1
                                        yScale: flipVBtn.checked ? -1 : 1
                                    },
                                    Rotation {
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        angle: win.quarterTurns * 90 + rotAngleSlider.value
                                    },
                                    Scale {   // 스트레이튼 채움 줌(원근 앞 — export 와 동일 순서 H∘Z∘R)
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: viewport.straightenZoom; yScale: viewport.straightenZoom
                                    },
                                    Matrix4x4 { matrix: viewport.perspMat },   // 원근(키스톤)+배율
                                    Scale {   // 화면 fit (최외곽)
                                        origin.x: viewport.procW / 2; origin.y: viewport.procH / 2
                                        xScale: viewport.fitScale; yScale: viewport.fitScale
                                    }
                                ]
                            }
                        }

                        // 날짜 스탬프(필름 데이트백) 오버레이 — cropClip(=최종 크롭 프레임) 우하단에
                        // source-over 합성. 위치/크기는 '최종(크롭) 프레임' 짧은 변 기준이라 크롭해도
                        // 프레임 코너에 일정 비율로 붙는다(원본 코너 기준 X). cropClip 자식이라 줌/팬에
                        // 함께 스케일. export(date_stamp.stamp_export, 동일 비율·source-over)와 정합.
                        //   - wRatio/hRatio = 스프라이트 (W,H)/짧은변 (TEXT_FRAC·글로우 패딩 포함)
                        //   - 마진 0.030 = date_stamp.MARGIN_FRAC, opacity 0.92 = STAMP_STRENGTH
                        // 크롭 편집 중·원본 비교 중에는 숨김.
                        Image {
                            id: stampOverlay
                            source: controller.stampUrl
                            cache: false; smooth: true
                            visible: win.dateStamp && controller.stampText !== ""
                                     && !viewport.cropEdit && !win.compareOn
                            opacity: 0.92
                            property real shortEdge: Math.min(cropClip.width, cropClip.height)
                            width: controller.stampWRatio * shortEdge
                            height: controller.stampHRatio * shortEdge
                            // 촬영 방향에 따른 코너 배치(데이트백 현실 반영) — 스프라이트는 이미
                            // controller 에서 회전돼 있어 여기선 코너 x/y 만 잡는다(export 와 동일).
                            property string corner: controller.stampCorner   // br/bl/tl/tr
                            property real margin: 0.030 * shortEdge
                            x: (corner === "br" || corner === "tr") ? parent.width - width - margin : margin
                            y: (corner === "br" || corner === "bl") ? parent.height - height - margin : margin
                        }
                    }

                    // 1:1 확대 & 패닝 입력(크롭 패널 외): 더블클릭=확대/해제(클릭점 중앙), 드래그=팬.
                    MouseArea {
                        anchors.fill: parent
                        enabled: !viewport.cropEdit && cropClip.visible
                        cursorShape: viewport.zoomed ? (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)
                                                     : Qt.ArrowCursor
                        property real _px: 0
                        property real _py: 0
                        onPressed: (m) => { _px = m.x; _py = m.y }
                        onPositionChanged: (m) => {
                            if (!pressed || !viewport.zoomed) return
                            viewport.panX += m.x - _px; viewport.panY += m.y - _py
                            _px = m.x; _py = m.y; viewport.clampPan()
                        }
                        onDoubleClicked: (m) => {
                            if (viewport.zoomed) viewport.resetZoom()
                            else viewport.zoomToPoint(m.x, m.y)
                        }
                    }

                    // === 미니맵(확대 시): 전체(크롭 결과) 중 현재 보이는 영역 표시. 우하단. ===
                    Item {
                        id: minimap
                        visible: viewport.zoomed && !viewport.cropEdit && cropClip.visible
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        property real maxMM: 180                       // 긴 변 최대 px
                        property real crW: Math.max(1, viewport.clipW) // 크롭 결과 표시 폭(zoom=1)
                        property real crH: Math.max(1, viewport.clipH)
                        property real mmScale: maxMM / Math.max(crW, crH)
                        width: crW * mmScale
                        height: crH * mmScale

                        // 배경: 전체 크롭 결과 썸네일(canvasHolder 를 줌/팬 없이 작게 복제)
                        Rectangle {
                            anchors.fill: parent
                            color: "#000000"
                            border.color: "#80ffffff"; border.width: 1
                            radius: 3
                            clip: true
                            ShaderEffectSource {
                                sourceItem: canvasHolder      // 지오메트리 변환된 캔버스(줌/팬은 cropClip 에 있어 미반영)
                                live: true
                                smooth: true
                                width: viewport.canvasDispW * minimap.mmScale
                                height: viewport.canvasDispH * minimap.mmScale
                                x: -win.cropX * viewport.canvasDispW * minimap.mmScale   // 크롭 영역만 보이게 오프셋
                                y: -win.cropY * viewport.canvasDispH * minimap.mmScale
                            }
                        }
                        // 현재 보이는 영역 사각형 (pan/zoom 으로부터 콘텐츠 대비 분율 계산)
                        Rectangle {
                            color: "#33ffd24a"
                            border.color: "#ffd24a"; border.width: 1.5
                            radius: 1
                            property real cw: viewport.clipW * viewport.zoomFactor   // 줌 콘텐츠 폭(px)
                            property real ch: viewport.clipH * viewport.zoomFactor
                            property real lf: Math.max(0, 0.5 - (viewport.panX + viewport.width / 2) / cw)
                            property real tf: Math.max(0, 0.5 - (viewport.panY + viewport.height / 2) / ch)
                            property real wf: Math.min(1, viewport.width / cw)
                            property real hf: Math.min(1, viewport.height / ch)
                            x: lf * minimap.width
                            y: tf * minimap.height
                            width: Math.min(minimap.width - x, wf * minimap.width)
                            height: Math.min(minimap.height - y, hf * minimap.height)
                        }
                    }

                    // === 크롭 편집 오버레이 (크롭 패널에서만): 핸들=리사이즈, 내부=이동,
                    //     네 꼭짓점 외곽 부근 드래그=회전(스트레이튼). 캔버스 위에 정렬. ===
                    Item {
                        id: cropOverlay
                        visible: viewport.cropEdit && cropClip.visible
                        anchors.centerIn: parent
                        width: viewport.canvasDispW
                        height: viewport.canvasDispH

                        property real bl: win.cropX * width      // 박스 px 경계
                        property real bt: win.cropY * height
                        property real bw: win.cropW * width
                        property real bh: win.cropH * height
                        property bool rotating: false            // 회전 드래그 중(촘촘한 격자)
                        property bool rotHover: false            // 회전 영역 호버(회전 커서)
                        property real rotPx: 0                   // 회전 커서 위치(오버레이 좌표)
                        property real rotPy: 0
                        property int rotCorner: 0                // 활성 회전 코너(0=NW,1=NE,2=SW,3=SE)

                        // (1) 바깥 어둡게(시각용, 마우스 비소비 -> 아래 회전 영역이 받음)
                        Rectangle { color: "#88000000"; x: 0; y: 0; width: parent.width; height: parent.bt }
                        Rectangle { color: "#88000000"; x: 0; y: parent.bt + parent.bh
                                    width: parent.width; height: parent.height - parent.bt - parent.bh }
                        Rectangle { color: "#88000000"; x: 0; y: parent.bt; width: parent.bl; height: parent.bh }
                        Rectangle { color: "#88000000"; x: parent.bl + parent.bw; y: parent.bt
                                    width: parent.width - parent.bl - parent.bw; height: parent.bh }

                        // (2) 회전 영역: 박스 네 꼭짓점 외곽 부근 드래그 -> 캔버스 중심 기준 각도변화.
                        //     박스 안쪽(이동)·정확한 코너(리사이즈 핸들)는 위에 있어 그쪽이 우선.
                        Repeater {
                            model: 4
                            delegate: MouseArea {
                                property int ci: index   // 0=NW,1=NE,2=SW,3=SE
                                // 드래그 중엔 영역을 크게 확장 -> 커서가 영역 밖으로 안 나가
                                // BlankCursor 가 끝까지 유지(OS 커서 재출현으로 인한 이중커서 방지).
                                property bool dragging: cropOverlay.rotating && cropOverlay.rotCorner === ci
                                property real cornerX: (ci === 1 || ci === 3) ? cropOverlay.bl + cropOverlay.bw : cropOverlay.bl
                                property real cornerY: (ci === 2 || ci === 3) ? cropOverlay.bt + cropOverlay.bh : cropOverlay.bt
                                width: dragging ? 8000 : 80
                                height: dragging ? 8000 : 80
                                x: dragging ? (cropOverlay.width / 2 - width / 2) : (cornerX - width / 2)
                                y: dragging ? (cropOverlay.height / 2 - height / 2) : (cornerY - height / 2)
                                hoverEnabled: true
                                cursorShape: Qt.BlankCursor     // 곡선 화살표(rotCursor)로 대체
                                property real startAng: 0
                                property real baseVal: 0
                                function angAt(m) {
                                    var p = mapToItem(cropOverlay, m.x, m.y)
                                    cropOverlay.rotPx = p.x; cropOverlay.rotPy = p.y
                                    return Math.atan2(p.y - cropOverlay.height / 2, p.x - cropOverlay.width / 2)
                                }
                                onEntered: {
                                    cropOverlay.rotCorner = ci
                                    cropOverlay.rotPx = x + width / 2; cropOverlay.rotPy = y + height / 2
                                    cropOverlay.rotHover = true
                                }
                                onExited: if (!pressed) cropOverlay.rotHover = false
                                onPressed: (mouse) => {
                                    cropOverlay.rotCorner = ci
                                    startAng = angAt(mouse)
                                    baseVal = rotAngleSlider.value
                                    cropOverlay.rotating = true
                                    cropOverlay.rotHover = true
                                }
                                onPositionChanged: (mouse) => {
                                    var a = angAt(mouse)
                                    if (pressed) {
                                        var d = (a - startAng) * 180.0 / Math.PI
                                        rotAngleSlider.value = Math.max(-45, Math.min(45, baseVal + d))
                                    }
                                }
                                onReleased: { cropOverlay.rotating = false; cropOverlay.rotHover = containsMouse }
                            }
                        }

                        // (3) 크롭 박스 테두리 + 격자 + 내부 이동
                        Rectangle {
                            id: boxRect
                            x: cropOverlay.bl; y: cropOverlay.bt
                            width: cropOverlay.bw; height: cropOverlay.bh
                            color: "transparent"; border.color: "#f0ffffff"; border.width: 1

                            // 기본 3분할 격자(회전 중에는 숨김 -> 촘촘한 격자만 표시)
                            Repeater { model: 2
                                Rectangle { visible: !cropOverlay.rotating; color: "#55ffffff"
                                            width: 1; height: boxRect.height
                                            x: boxRect.width * (index + 1) / 3 } }
                            Repeater { model: 2
                                Rectangle { visible: !cropOverlay.rotating; color: "#55ffffff"
                                            height: 1; width: boxRect.width
                                            y: boxRect.height * (index + 1) / 3 } }

                            // 회전 중에만: 촘촘한 정사각 격자(수평/수직 정렬 보조). 고정 px 셀 = 정사각.
                            Item {
                                anchors.fill: parent
                                visible: cropOverlay.rotating
                                property int cell: 26
                                Repeater { model: Math.max(0, Math.floor(boxRect.width / 26))
                                    Rectangle { color: "#33ffffff"; width: 1; height: boxRect.height; x: (index + 1) * 26 } }
                                Repeater { model: Math.max(0, Math.floor(boxRect.height / 26))
                                    Rectangle { color: "#33ffffff"; height: 1; width: boxRect.width; y: (index + 1) * 26 } }
                            }

                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true       // 박스 내부 호버 소비 -> 회전 커서 안 뜸
                                cursorShape: Qt.SizeAllCursor
                                property real ox: 0
                                property real oy: 0
                                onPressed: (mouse) => { ox = mouse.x; oy = mouse.y }
                                onPositionChanged: (mouse) => {
                                    if (!pressed) return        // 호버만으로는 이동 안 함(클릭&드래그 전용)
                                    var dx = (mouse.x - ox) / cropOverlay.width
                                    var dy = (mouse.y - oy) / cropOverlay.height
                                    win.setCropRect(win.cropX + dx, win.cropY + dy, win.cropW, win.cropH)
                                }
                            }
                        }

                        // (4) 핸들: 자유=8(모서리+변), 종횡비 잠금=4(모서리만). 정확한 코너 = 리사이즈.
                        Repeater {
                            model: win.cropAspect > 0 ? 4 : 8
                            delegate: Rectangle {
                                property int hi: index
                                property bool hl: hi === 0 || hi === 2 || hi === 6   // left
                                property bool hr: hi === 1 || hi === 3 || hi === 7   // right
                                property bool ht: hi === 0 || hi === 1 || hi === 4   // top
                                property bool hb: hi === 2 || hi === 3 || hi === 5   // bottom
                                width: 13; height: 13; radius: 2
                                color: "#f0ffffff"; border.color: "#333"; border.width: 1
                                x: (hl ? cropOverlay.bl : hr ? cropOverlay.bl + cropOverlay.bw
                                                              : cropOverlay.bl + cropOverlay.bw / 2) - width / 2
                                y: (ht ? cropOverlay.bt : hb ? cropOverlay.bt + cropOverlay.bh
                                                              : cropOverlay.bt + cropOverlay.bh / 2) - height / 2

                                MouseArea {
                                    anchors.fill: parent
                                    anchors.margins: -6     // 잡기 쉽게 확장
                                    hoverEnabled: true      // 코너 호버 소비 -> 회전 커서 안 뜸(여기선 리사이즈)
                                    cursorShape: (parent.hl && parent.ht) || (parent.hr && parent.hb) ? Qt.SizeFDiagCursor
                                               : (parent.hr && parent.ht) || (parent.hl && parent.hb) ? Qt.SizeBDiagCursor
                                               : (parent.hl || parent.hr) ? Qt.SizeHorCursor : Qt.SizeVerCursor
                                    onPositionChanged: (mouse) => {
                                        if (!pressed) return    // 호버만으로는 리사이즈 안 함(클릭&드래그 전용)
                                        var p = mapToItem(cropOverlay, mouse.x, mouse.y)
                                        var nx = Math.max(0, Math.min(1, p.x / cropOverlay.width))
                                        var ny = Math.max(0, Math.min(1, p.y / cropOverlay.height))
                                        if (win.cropAspect > 0) {
                                            // 잠금(모서리): 반대 코너 고정, 너비로 높이 결정
                                            var ax = parent.hl ? (win.cropX + win.cropW) : win.cropX
                                            var ay = parent.ht ? (win.cropY + win.cropH) : win.cropY
                                            var nw = Math.abs(nx - ax)
                                            var kn = win.cropAspect / Math.max(0.0001, viewport.cA)
                                            var nh = nw / kn
                                            var newL = parent.hl ? (ax - nw) : ax
                                            var newT = parent.ht ? (ay - nh) : ay
                                            win.setCropRect(newL, newT, nw, nh)
                                        } else {
                                            var L = win.cropX, T = win.cropY
                                            var R = win.cropX + win.cropW, B = win.cropY + win.cropH
                                            if (parent.hl) L = nx
                                            if (parent.hr) R = nx
                                            if (parent.ht) T = ny
                                            if (parent.hb) B = ny
                                            win.setCropRect(Math.min(L, R), Math.min(T, B),
                                                            Math.abs(R - L), Math.abs(B - T))
                                        }
                                    }
                                }
                            }
                        }

                        // (5) 회전 커서: 곡선 화살표(BlankCursor 대체). 호는 짧고(~150°), 열린 구간이
                        //     코너별 바깥 대각선(박스 반대쪽)을 향함. 회전영역 호버/드래그 시 마우스 추적.
                        Canvas {
                            id: rotCursor
                            visible: cropOverlay.rotHover || cropOverlay.rotating
                            width: 30; height: 30; z: 100
                            x: cropOverlay.rotPx - width / 2
                            y: cropOverlay.rotPy - height / 2
                            property int corner: cropOverlay.rotCorner
                            onCornerChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var cc = width / 2, r = 8.5
                                // 바깥 대각선 방향(코너별로 다름) = 호의 중심, 열린 구간은 반대(박스쪽).
                                var dx = (corner === 1 || corner === 3) ? 1 : -1
                                var dy = (corner === 2 || corner === 3) ? 1 : -1
                                var base = Math.atan2(dy, dx)
                                var span = 2.4                     // ~138°
                                var a0 = base - span / 2, a1 = base + span / 2
                                ctx.lineCap = "round"
                                for (var pass = 0; pass < 2; pass++) {
                                    ctx.lineWidth = (pass === 0) ? 3.0 : 1.6
                                    ctx.strokeStyle = (pass === 0) ? "#202020" : "#ffffff"
                                    ctx.beginPath(); ctx.arc(cc, cc, r, a0, a1); ctx.stroke()
                                    // 호 양 끝 화살촉(접선 방향) -> 회전 의미
                                    var ends = [[a1, a1 + Math.PI / 2], [a0, a0 - Math.PI / 2]]
                                    for (var i = 0; i < 2; i++) {
                                        var ea = ends[i][0], ta = ends[i][1], s = 3.8, b = 0.40
                                        var ex = cc + r * Math.cos(ea), ey = cc + r * Math.sin(ea)
                                        ctx.beginPath()
                                        ctx.moveTo(ex, ey); ctx.lineTo(ex - s * Math.cos(ta - b), ey - s * Math.sin(ta - b))
                                        ctx.moveTo(ex, ey); ctx.lineTo(ex - s * Math.cos(ta + b), ey - s * Math.sin(ta + b))
                                        ctx.stroke()
                                    }
                                }
                            }
                        }
                    }


                    Text {
                        visible: srcImage.status !== Image.Ready
                        anchors.centerIn: parent
                        color: "#888"
                        font.pixelSize: 16
                        text: "Double-click a RAF file in the explorer on the left to open it"
                    }

                    // 원본 비교 버튼: 클릭(또는 \ 키)으로 원본↔편집본 토글(좌하단). 크롭 페이지에선 숨김.
                    Rectangle {
                        visible: controller.imagePath !== "" && win.activePanel === 0
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        anchors.margins: 12
                        radius: 6
                        color: win.compareOn ? "#cc8ab4f8" : "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: cmpRow.implicitWidth + 20
                        height: cmpRow.implicitHeight + 14
                        RowLayout {
                            id: cmpRow
                            anchors.centerIn: parent
                            spacing: 6
                            Label {
                                text: win.compareOn ? "Viewing original" : "Compare original"
                                color: win.compareOn ? "#10243f" : "#e6e6e6"
                                font.pixelSize: 11; font.bold: true
                            }
                            Label {
                                text: "(\\)"
                                color: win.compareOn ? "#10243f" : "#9a9a9a"
                                font.pixelSize: 10
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: win.compareOn = !win.compareOn
                        }
                    }

                    // 원본 표시 배지: 원본 보는 중 상단중앙에 표시.
                    Rectangle {
                        visible: win.compareOn
                        anchors.top: parent.top
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.topMargin: 12
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#8ab4f8"; border.width: 1
                        width: cmpBadge.implicitWidth + 20
                        height: cmpBadge.implicitHeight + 12
                        Label {
                            id: cmpBadge
                            anchors.centerIn: parent
                            text: "Original · BEFORE"
                            color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                            font.capitalization: Font.AllUppercase
                        }
                    }

                    // 촬영정보 플로팅 패널 (I 키 토글) — 좌측 뷰 왼쪽 끝에 고정
                    Rectangle {
                        visible: win.infoOverlay && cropClip.visible
                                 && controller.shootingInfo.length > 0
                        anchors.left: parent.left
                        anchors.top: parent.top
                        anchors.margins: 12
                        radius: 6
                        color: "#cc1e1e1e"
                        border.color: "#55ffffff"; border.width: 1
                        width: ovCol.implicitWidth + 24
                        height: ovCol.implicitHeight + 20
                        ColumnLayout {
                            id: ovCol
                            anchors.centerIn: parent
                            spacing: 3
                            Label {
                                text: "Shooting Info  (I)"
                                color: "#8ab4f8"; font.pixelSize: 11; font.bold: true
                                font.capitalization: Font.AllUppercase
                                Layout.bottomMargin: 3
                            }
                            Repeater {
                                model: controller.shootingInfo
                                delegate: RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 16
                                    Label {
                                        text: modelData.label
                                        color: "#9a9a9a"; font.pixelSize: 11
                                    }
                                    Item { Layout.fillWidth: true }
                                    Label {
                                        text: modelData.value
                                        color: "#e6e6e6"; font.pixelSize: 11
                                        horizontalAlignment: Text.AlignRight
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // 진행 중 스피너 오버레이 (이미지 위): export / 디코딩(렌즈 보정) / 하늘 세그멘테이션
            Rectangle {
                anchors.fill: parent
                visible: controller.exporting || controller.busy || controller.skyBusy
                color: "#aa000000"
                MouseArea { anchors.fill: parent }   // 진행 중 이미지 입력 차단
                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12
                    BusyIndicator {
                        running: controller.exporting || controller.busy || controller.skyBusy
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 64; implicitHeight: 64
                    }
                    Label {
                        text: controller.segStatus !== "" ? controller.segStatus
                              : controller.exporting ? "Exporting…"
                              : (controller.skyBusy ? "Detecting mask…" : "Processing…")
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }

        // ---------- 우측 패널 (헤더 고정 + 패널 전환 스택) ----------
        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: "#2b2b2b"

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                // ── 고정 헤더: 패널과 무관한 전역 동작(Export/해상도/상태). 항상 보임 ──
                ColumnLayout {
                    id: panelHeader
                    Layout.fillWidth: true
                    Layout.leftMargin: 16
                    Layout.rightMargin: 16
                    Layout.topMargin: 16
                    spacing: 12

                // 편집 도구 줄(맨 위): Undo/Redo(좌) — 스페이서 — Reset/복사붙여넣기(우)
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        text: "↶"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter; padding: 0; font.pixelSize: 14
                        enabled: win.canUndo
                        ToolTip.visible: hovered; ToolTip.text: "Undo (Ctrl+Z)"
                        onClicked: win.undo()
                    }
                    Button {
                        text: "↷"
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter; padding: 0; font.pixelSize: 14
                        enabled: win.canRedo
                        ToolTip.visible: hovered; ToolTip.text: "Redo (Ctrl+Shift+Z)"
                        onClicked: win.redo()
                    }
                    Item { Layout.fillWidth: true }      // 좌(이력) ↔ 우(초기화/기타) 분리 스페이서
                    Button {
                        id: resetBtn
                        text: "↺"                       // Reset 아이콘(조절 초기화)
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26       // 작은 정사각
                        Layout.alignment: Qt.AlignVCenter
                        padding: 0
                        font.pixelSize: 14
                        ToolTip.visible: hovered
                        ToolTip.text: "Reset (clear adjustments — including geometry)"
                        onClicked: win.resetAndClearEdits()   // 모든 편집 초기화 + 사이드카 삭제(파일명 앰버 해제)
                    }
                    // 편집 복사/붙여넣기 메뉴(이미지 간) — Reset 우측 "⋯" 드롭다운.
                    Button {
                        id: editClipBtn
                        text: "⋯"
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26
                        Layout.alignment: Qt.AlignVCenter
                        padding: 0
                        font.pixelSize: 14
                        enabled: controller.imagePath !== ""
                        ToolTip.visible: hovered
                        ToolTip.text: "Copy / paste edits (between images)"
                        onClicked: editClipMenu.popup(0, height)
                        Menu {
                            id: editClipMenu
                            MenuItem { text: "Copy all"; onTriggered: win.copyEdits(false) }
                            MenuItem { text: "Copy (excluding WB · Tint)"; onTriggered: win.copyEdits(true) }
                            MenuItem {
                                text: "Paste"
                                enabled: win._editClipboard !== null
                                onTriggered: win.pasteEdits()
                            }
                        }
                    }
                }

                // 출력: 주 버튼 + 옵션(⚙) 팝업
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        id: exportMainBtn
                        text: "Export…"
                        Layout.fillWidth: true
                        enabled: controller.imagePath !== ""
                        onClicked: {
                            // 기본 파일명 = '<원본이름>_exported.png' (원본과 같은 폴더)
                            var u = controller.suggestedExportUrl()
                            if (u != "") saveDialog.selectedFile = u
                            saveDialog.open()
                        }
                    }
                    Button {
                        id: exportOptBtn
                        text: "▾"                       // Export 옵션 토글(펼치기)
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: exportMainBtn.height   // Export 버튼과 높이 동일하게 고정
                        padding: 0; font.pixelSize: 14
                        ToolTip.visible: hovered
                        ToolTip.text: "Export options (resolution · render · 16-bit)"
                        onClicked: exportOptPopup.opened ? exportOptPopup.close() : exportOptPopup.open()
                        Popup {
                            id: exportOptPopup
                            y: exportOptBtn.height + 4
                            x: exportOptBtn.width - width    // 버튼 오른쪽에 맞춰 좌측으로 펼침(패널 안)
                            width: 230
                            padding: 10
                            modal: false
                            closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                            background: Rectangle { color: "#2b2b2b"; border.color: "#555"; border.width: 1; radius: 6 }
                            contentItem: ColumnLayout {
                                spacing: 10
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Resolution"; color: "white"; font.pixelSize: 12; Layout.preferredWidth: 72 }
                                    ComboBox {
                                        id: resCombo
                                        Layout.fillWidth: true
                                        currentIndex: 0     // 원본
                                        model: ["Original (Full)", "4096", "3840 (4K)",
                                                "2560", "2048", "1920 (FHD)", "1280"]
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    Label { text: "Render"; color: "white"; font.pixelSize: 12; Layout.preferredWidth: 72 }
                                    ComboBox {
                                        id: renderModeCombo
                                        Layout.fillWidth: true
                                        // 16bit 는 CPU 전용(GPU grab 은 8bit) → 16bit 체크 시 GPU 비활성/CPU 고정
                                        enabled: !bitDepth16Check.checked
                                        currentIndex: 0     // 기본 CPU
                                        model: ["CPU", "GPU"]
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 6
                                    CheckBox {
                                        id: bitDepth16Check
                                        ToolTip.visible: hovered
                                        ToolTip.text: "Save 16-bit/channel (preserves gradation · headroom). TIFF recommended. CPU render only."
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: "16-bit (TIFF/PNG · CPU)"
                                        color: "white"; font.pixelSize: 12
                                        verticalAlignment: Text.AlignVCenter
                                    }
                                }
                            }
                        }
                    }
                }

                Label {
                    Layout.fillWidth: true
                    visible: controller.exportStatus !== ""
                    color: "#9fd39f"
                    font.pixelSize: 11
                    wrapMode: Text.WrapAnywhere
                    text: controller.exportStatus
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }
                }   // end panelHeader

                // ── 패널 전환 스택 (Edit / Rotation / Crop) ──
                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: win.activePanel

                    // ===== index 0: Edit (기존 편집 컨트롤 전부, 스크롤) =====
                    Flickable {
                        id: panelScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: panelCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        // 다크 테마 스크롤바 (Flickable + 명시적 Basic ScrollBar -> 확실히 표시)
                        ScrollBar.vertical: B.ScrollBar {
                            id: vbar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle {
                                implicitWidth: 8
                                radius: 4
                                color: vbar.pressed ? "#cfcfcf" : "#9a9a9a"   // 밝게(항상 보임)
                            }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: panelCol
                            x: 16; y: 16
                            width: panelScroll.width - 32
                            spacing: 12

                // ── 접이식 섹션: 헤더 클릭으로 내용 토글 ──
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[0] ? "▾  " : "▸  ") + "Film Simulation"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(0) }
                }
                ColumnLayout {
                    visible: win.secOpen[0]
                    Layout.fillWidth: true
                    spacing: 12
                ComboBox {
                    id: simCombo
                    Layout.fillWidth: true
                    currentIndex: 0
                    onActivated: win.refreshHistogram()
                    // 라벨은 win.simLabels(= controller.filmSims 파생). 인덱스→simKeys[i]→image://lut/<key>
                    model: win.simLabels
                    // 그룹 구분선: 행(인덱스)을 추가하지 않고 그룹 시작 항목 위에 선만 그림
                    // → simKeys 매핑·저장된 simIndex(사이드카) 그대로 호환.
                    // 그룹 구분선: controller.filmSims 의 group 이 바뀌는 인덱스(존재하는 시뮬 기준 자동).
                    readonly property var simGroupStarts: {
                        var arr = []; var sims = controller.filmSims
                        for (var i = 1; i < sims.length; i++)
                            if (sims[i].group !== sims[i - 1].group) arr.push(i)
                        return arr
                    }
                    delegate: ItemDelegate {
                        id: simDel
                        width: ListView.view ? ListView.view.width : simCombo.width
                        required property int index
                        required property var modelData
                        text: modelData
                        highlighted: simCombo.highlightedIndex === index
                        property bool groupStart: simCombo.simGroupStarts.indexOf(index) !== -1
                        contentItem: Text {
                            text: simDel.text
                            color: "#e8e8e8"; font.pixelSize: 13
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            color: simDel.highlighted ? "#3a3f4b" : "#2b2b2b"
                            Rectangle {                       // 그룹 구분선(항목 상단)
                                visible: simDel.groupStart
                                anchors { top: parent.top; left: parent.left; right: parent.right }
                                height: 1; color: "#555"
                            }
                        }
                    }
                    // 팝업도 다크로 직접 스타일(네이티브 팝업은 커스텀 delegate 와 안 맞음)
                    popup: Popup {
                        y: simCombo.height
                        width: simCombo.width
                        implicitHeight: Math.min(contentItem.implicitHeight + 2, 380)
                        padding: 1
                        background: Rectangle { color: "#2b2b2b"; border.color: "#555"; radius: 3 }
                        contentItem: ListView {
                            clip: true
                            implicitHeight: contentHeight
                            model: simCombo.delegateModel
                            currentIndex: simCombo.highlightedIndex
                            ScrollIndicator.vertical: ScrollIndicator {}
                        }
                    }
                }

                Label {
                    text: "Strength:  " + Math.round(simStrengthSlider.value * 100) + "%"
                    color: "white"
                    enabled: simCombo.currentIndex !== 0
                }
                Slider {
                    id: simStrengthSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: 0.0; to: 1.0; value: 1.0
                    enabled: simCombo.currentIndex !== 0   // None 이면 비활성
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(simStrengthSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[1] ? "▾  " : "▸  ") + "Light"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(1) }
                }
                ColumnLayout {
                    visible: win.secOpen[1]
                    Layout.fillWidth: true
                    spacing: 12

                Label {
                    text: "Exposure:  " + expSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: expSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -3.0; to: 3.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(expSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Contrast:  " + conSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: conSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: 0.5; to: 2.0; value: 1.0
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(conSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Highlights:  " + hiSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: hiSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hiSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Shadows:  " + shSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: shSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(shSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Whites:  " + whSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: whSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(whSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                Label {
                    text: "Blacks:  " + blSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: blSlider
                    Layout.fillWidth: true
                    onMoved: win.refreshHistogram()
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(blSlider)
                        else { if (_pendingReset) { value = defaultValue; _pendingReset = false } win.refreshHistogram() }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[2] ? "▾  " : "▸  ") + "Tone Curve"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(2) }
                }
                ColumnLayout {
                    visible: win.secOpen[2]
                    Layout.fillWidth: true
                    spacing: 12
                // 채널 선택: RGB(마스터) / R / G / B
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    Repeater {
                        model: [{t: "RGB", c: "#e8e8e8"}, {t: "R", c: "#ff6b6b"},
                                {t: "G", c: "#5fd16a"}, {t: "B", c: "#5b9cff"}]
                        delegate: Rectangle {
                            required property int index
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 26
                            radius: 4
                            color: curveEditor.channel === index ? "#3a3a3a" : "#2a2a2a"
                            border.color: curveEditor.channel === index ? modelData.c : "#444"
                            Text {
                                anchors.centerIn: parent
                                text: modelData.t; color: modelData.c
                                font.pixelSize: 12; font.bold: curveEditor.channel === index
                            }
                            TapHandler { onTapped: curveEditor.channel = index }
                        }
                    }
                }
                CurveEditor {
                    id: curveEditor
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240     // 고정 높이(너비에서 분리: 레이아웃 루프 방지)
                    histogram: controller.histogram
                    onEdited: { controller.setCurve(allLuts()); win.refreshHistogram() }
                }

                // 클리핑 경고 오버레이 토글(프리뷰 전용): 하이라이트=빨강 / 섀도=파랑.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: clipWarnCheck
                        checked: win.clipWarn
                        onToggled: win.clipWarn = checked
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Clipping warning  (J) — highlights red / shadows blue"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                // 디스플레이 색관리(프리뷰 전용): 광색역 모니터에서 sRGB 를 정확히 표시.
                // 모니터 ICC 프로파일이 광색역일 때만 노출(sRGB 모니터에선 무의미).
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    visible: controller.hasDisplayCM
                    CheckBox {
                        id: displayCmCheck
                        checked: win.displayCM
                        onToggled: win.displayCM = checked
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Display color management  (Ctrl+Shift+M) — match monitor gamut"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[3] ? "▾  " : "▸  ") + "White Balance"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(3) }
                }
                ColumnLayout {
                    visible: win.secOpen[3]
                    Layout.fillWidth: true
                    spacing: 12

                Label {
                    text: "Temp:  " + Math.round(tempSlider.value) + " K"
                            + "   (as-shot " + controller.asShotKelvin + "K)"
                    color: "white"
                }
                Slider {
                    id: tempSlider
                    Layout.fillWidth: true
                    from: 2000; to: 12000; value: 6500
                    stepSize: 50
                    // 더블클릭 -> as-shot 색온도로 리셋
                    property real defaultValue: controller.asShotKelvin
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    // press: 더블 여부 판정. release: (더블이면 리셋 후) 재디코딩 커밋.
                    onPressedChanged: {
                        if (pressed) {
                            _pendingReset = win.isDblPress(tempSlider)
                        } else {
                            if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            controller.setWb(tempSlider.value, tintSlider.value)
                        }
                    }
                    onValueChanged: if (!pressed && !win._applying) wbTimer.restart()
                }

                Label {
                    text: "Tint:  " + tintSlider.value.toFixed(2) + "  (− green / + magenta)"
                    color: "white"
                }
                Slider {
                    id: tintSlider
                    Layout.fillWidth: true
                    from: -1.5; to: 1.5; value: 0.0    // as-shot 추정 tint(최대 ±1.5) 수용
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) {
                            _pendingReset = win.isDblPress(tintSlider)
                        } else {
                            if (_pendingReset) { value = defaultValue; _pendingReset = false }
                            controller.setWb(tempSlider.value, tintSlider.value)
                        }
                    }
                    onValueChanged: if (!pressed && !win._applying) wbTimer.restart()
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[4] ? "▾  " : "▸  ") + "Color"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(4) }
                }
                ColumnLayout {
                    visible: win.secOpen[4]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Vibrance:  " + vibSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: vibSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(vibSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Saturation:  " + satSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: satSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(satSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[5] ? "▾  " : "▸  ") + "Color Mixer"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(5) }
                }
                ColumnLayout {
                    visible: win.secOpen[5]
                    Layout.fillWidth: true
                    spacing: 12
                // 8색상대 스와치(클릭=선택). 선택 대역은 흰 테두리.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 3
                    Repeater {
                        model: 8
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 22
                            radius: 3
                            color: Qt.hsva(index / 8.0, 0.85, 0.95, 1.0)
                            border.width: win.hslBand === index ? 2 : 0
                            border.color: "#ffffff"
                            MouseArea { anchors.fill: parent; onClicked: win.hslBand = index }
                        }
                    }
                }
                Label { text: "Hue:  " + Math.round(win.hslH[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslHueSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslH[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslHueSlider.value = win.hslH[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslH", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslHueSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslH", 0.0); _pendingReset = false }
                    }
                }
                Label { text: "Saturation:  " + Math.round(win.hslS[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslSatSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslS[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslSatSlider.value = win.hslS[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslS", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslSatSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslS", 0.0); _pendingReset = false }
                    }
                }
                Label { text: "Luminance:  " + Math.round(win.hslL[win.hslBand] * 100); color: "white" }
                Slider {
                    id: hslLumSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0
                    Component.onCompleted: value = win.hslL[win.hslBand]
                    Connections { target: win; function onHslBandChanged() { hslLumSlider.value = win.hslL[win.hslBand] } }
                    onMoved: win.setHslBandValue("hslL", value)
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hslLumSlider)
                        else if (_pendingReset) { value = 0.0; win.setHslBandValue("hslL", 0.0); _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // ===== Color Grading (스플릿 토닝) — 섹션 인덱스 11 =====
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[11] ? "▾  " : "▸  ") + "Color Grading"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(11) }
                }
                ColumnLayout {
                    visible: win.secOpen[11]
                    Layout.fillWidth: true
                    spacing: 6

                    // 섀도 — Hue(0..360°) + Sat(0..100). Sat=0 이면 무효과. 스와치=적용 색 미리보기.
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Shadows"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgShHueSlider.value) + "°  S " + Math.round(cgShSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgShHueSlider.value/360, cgShSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgShHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgShHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgShSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgShSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 미드톤
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Midtones"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgMidHueSlider.value) + "°  S " + Math.round(cgMidSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgMidHueSlider.value/360, cgMidSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgMidHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgMidHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgMidSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgMidSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 하이라이트
                    RowLayout {
                        Layout.fillWidth: true; spacing: 6
                        Label { text: "Highlights"; color: "white"; font.pixelSize: 12; font.bold: true }
                        Item { Layout.fillWidth: true }
                        Label { text: "H " + Math.round(cgHiHueSlider.value) + "°  S " + Math.round(cgHiSatSlider.value*100); color: "#aaa"; font.pixelSize: 11 }
                        Rectangle { width: 26; height: 14; radius: 3; border.color: "#666"; border.width: 1
                                    color: Qt.hsva(cgHiHueSlider.value/360, cgHiSatSlider.value, 1, 1) }
                    }
                    HueBar { Layout.fillWidth: true; Layout.preferredHeight: 8 }
                    Slider {
                        id: cgHiHueSlider; Layout.fillWidth: true; from: 0; to: 360; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgHiHueSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    Slider {
                        id: cgHiSatSlider; Layout.fillWidth: true; from: 0; to: 1; value: 0; property real _lastPressMs: 0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgHiSatSlider); else if (_pendingReset) { value = 0; _pendingReset = false } }
                    }
                    // 밸런스: 섀도↔하이라이트 마스크 분포 이동(+ = 하이라이트 쪽).
                    Label { text: "Balance   " + cgBalanceSlider.value.toFixed(2); color: "white"; font.pixelSize: 12 }
                    Slider {
                        id: cgBalanceSlider; Layout.fillWidth: true; from: -1.0; to: 1.0; value: 0.0; property real _lastPressMs: 0
                        property real defaultValue: 0.0
                        property bool _pendingReset: false
                        onPressedChanged: { if (pressed) _pendingReset = win.isDblPress(cgBalanceSlider); else if (_pendingReset) { value = defaultValue; _pendingReset = false } }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[6] ? "▾  " : "▸  ") + "Detail & Vignette"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(6) }
                }
                ColumnLayout {
                    visible: win.secOpen[6]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Texture:  " + texSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: texSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(texSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Clarity:  " + claritySlider.value.toFixed(2); color: "white" }
                Slider {
                    id: claritySlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(claritySlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Dehaze:  " + dehazeSlider.value.toFixed(2); color: "white" }
                Slider {
                    id: dehazeSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(dehazeSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label {
                    text: "Vignette:  " + vignetteSlider.value.toFixed(2) + "  (− darker)"
                    color: "white"
                }
                Slider {
                    id: vignetteSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(vignetteSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[7] ? "▾  " : "▸  ") + "Grain"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(7) }
                }
                ColumnLayout {
                    visible: win.secOpen[7]
                    Layout.fillWidth: true
                    spacing: 12
                Label {
                    text: "Grain:  " + grainSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: grainSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Grain Size:  " + grainSizeSlider.value.toFixed(2) + "  (fine ↔ coarse)"
                    color: "white"
                }
                Slider {
                    id: grainSizeSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.5
                    property real defaultValue: 0.5
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(grainSizeSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[8] ? "▾  " : "▸  ") + "Sharpening"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(8) }
                }
                ColumnLayout {
                    visible: win.secOpen[8]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Amount:  " + Math.round(sharpAmtSlider.value * 100); color: "white" }
                Slider {
                    id: sharpAmtSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpAmtSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Radius:  " + sharpRadiusSlider.value.toFixed(1) + " px"; color: "white" }
                Slider {
                    id: sharpRadiusSlider
                    Layout.fillWidth: true
                    from: 0.5; to: 3.0; value: 1.0
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpRadiusSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Detail:  " + Math.round(sharpDetailSlider.value * 100); color: "white" }
                Slider {
                    id: sharpDetailSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.25
                    property real defaultValue: 0.25
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpDetailSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Masking:  " + Math.round(sharpMaskSlider.value * 100); color: "white" }
                Slider {
                    id: sharpMaskSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(sharpMaskSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                // ===== Noise Reduction — 섹션 인덱스 12 (텍스처/샤프닝 앞 단계) =====
                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[12] ? "▾  " : "▸  ") + "Noise Reduction"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(12) }
                }
                ColumnLayout {
                    visible: win.secOpen[12]
                    Layout.fillWidth: true
                    spacing: 12
                Label { text: "Luminance:  " + Math.round(lumaNrSlider.value * 100); color: "white" }
                Slider {
                    id: lumaNrSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(lumaNrSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                Label { text: "Color:  " + Math.round(colorNrSlider.value * 100); color: "white" }
                Slider {
                    id: colorNrSlider
                    Layout.fillWidth: true
                    from: 0.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(colorNrSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[9] ? "▾  " : "▸  ") + "Lens Corrections"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(9) }
                }
                ColumnLayout {
                    visible: win.secOpen[9]
                    Layout.fillWidth: true
                    spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: lensCheck
                        checked: controller.lensCorrection
                        onToggled: controller.setLensCorrection(checked)
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "X100V profile (distortion · vignetting · CA)"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                RowLayout {
                    Layout.fillWidth: true
                    Label {
                        Layout.fillWidth: true
                        text: (win.secOpen[10] ? "▾  " : "▸  ") + "Date Stamp"
                        color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                        font.capitalization: Font.AllUppercase
                    }
                    TapHandler { onTapped: win.toggleSec(10) }
                }
                ColumnLayout {
                    visible: win.secOpen[10]
                    Layout.fillWidth: true
                    spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    CheckBox {
                        id: stampCheck
                        enabled: controller.imagePath !== ""
                        checked: win.dateStamp
                        onToggled: win.dateStamp = checked
                    }
                    Label {
                        Layout.fillWidth: true
                        text: "Film date stamp  (D)"
                        color: stampCheck.enabled ? "white" : "#777"
                        font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                // 날짜 직접 입력(기본값=EXIF). 변경 시 디바운스 후 프리뷰 재렌더.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "Date"; color: "white"; font.pixelSize: 12 }
                    TextField {
                        id: stampField
                        Layout.fillWidth: true
                        enabled: win.dateStamp && controller.imagePath !== ""
                        placeholderText: "'YY MM DD  (e.g. '24 05 12)"
                        onTextEdited: stampDebounce.restart()
                        // 포커스가 잡히면 알파벳 단축키(I/D/B/L 등)를 입력으로 먹으므로,
                        // Enter=확정/Esc=취소 시 포커스를 풀어 단축키가 다시 동작하게 함.
                        onAccepted: { stampDebounce.stop(); controller.setStampText(text); focus = false }
                        Keys.onEscapePressed: focus = false
                        // hover 시 텍스트(I-beam) 커서. HoverHandler 는 hover 만 관찰하므로
                        // 클릭/드래그 선택/편집에 일절 관여하지 않음(MouseArea 는 드래그를 가로챔).
                        HoverHandler {
                            enabled: stampField.enabled
                            cursorShape: Qt.IBeamCursor
                        }
                    }
                }
                Timer {
                    id: stampDebounce
                    interval: 200
                    onTriggered: controller.setStampText(stampField.text)
                }
                Connections {
                    target: controller
                    // 새 파일 디코딩 완료 후 편집 복원/초기화(controller 가 fresh-load 1회만 발화).
                    function onEditsReady() {
                        // 새 파일 *디코딩 완료* 후: 저장된 편집이 있으면 복원, 없으면 기본값으로 초기화.
                        // (디코딩 전 트리거 금지 — 이전 이미지에 새 편집이 잘못 반영되는 것 방지)
                        win._applying = true
                        var e = controller.editsForCurrent()
                        if (e && e.v !== undefined) {
                            win.applyEdits(e)
                        } else {
                            win.resetAllEdits()
                            stampField.text = controller.stampText
                        }
                        win._applying = false
                        // 로드 전환 중 예약됐을 수 있는 자동저장 취소(fresh-load 는 사이드카를 새로
                        // 만들지 않는다 — 저장본 있으면 복원만, 없으면 기본값 유지). 주황 배지 오발 방지.
                        editSaveTimer.stop()
                        win.refreshHistogram()
                        win.histReset(JSON.stringify(win.editParams()))   // 로드 상태 = undo baseline
                    }
                }
                }   // end Date Stamp section

                        }   // end panelCol
                    }       // end Flickable (Edit 페이지)

                    // ===== index 1: Crop / Rotate / Geometry (UI 골격만, 변환은 다음 단계) =====
                    Flickable {
                        id: geoScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: geoCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: geoBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: geoBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: geoCol
                            x: 16; y: 16
                            width: geoScroll.width - 32
                            spacing: 12

                            // ---- Crop ----
                            Label {
                                text: "Crop"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label { text: "Aspect Ratio"; color: "white"; font.pixelSize: 12 }
                            ComboBox {
                                id: aspectCombo
                                Layout.fillWidth: true
                                currentIndex: 0
                                model: ["Original", "Free", "1:1",
                                        "3:2", "4:3", "16:9", "5:4"]
                                // 고정 비율 선택 -> 박스를 그 비율 중앙 최대로. 원본/자유 -> 전체로.
                                onActivated: {
                                    if (win.cropAspect > 0) win.applyCropAspect()
                                    else win.resetCropRect()
                                }
                            }
                            Label { text: "Orientation"; color: "white"; font.pixelSize: 12 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button { id: cropLandscapeBtn; text: "Landscape"; checkable: true; checked: true; autoExclusive: true; Layout.fillWidth: true; Layout.preferredWidth: 0
                                         onClicked: win.applyCropAspect() }
                                Button { id: cropPortraitBtn; text: "Portrait"; checkable: true; autoExclusive: true; Layout.fillWidth: true; Layout.preferredWidth: 0
                                         onClicked: win.applyCropAspect() }
                            }
                            // 안내
                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: "On the image: box handles = resize, drag inside = move, drag near a corner = rotate."
                                color: "#9a9a9a"; font.pixelSize: 11
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Rotate ----
                            Label {
                                text: "Rotate"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }

                            Label {
                                text: "Angle (Straighten):  "
                                      + (rotAngleSlider.value >= 0 ? "+" : "")
                                      + rotAngleSlider.value.toFixed(1) + "°"
                                color: "white"
                            }
                            Slider {
                                id: rotAngleSlider
                                Layout.fillWidth: true
                                from: -45.0; to: 45.0; value: 0.0
                                property real defaultValue: 0.0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(rotAngleSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }

                            Label {
                                text: "Rotate 90°" + (win.quarterTurns !== 0 ? "  (" + (win.quarterTurns * 90) + "°)" : "")
                                color: "white"; font.pixelSize: 12
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button {
                                    text: "⟲ 90°"
                                    Layout.fillWidth: true
                                    ToolTip.visible: hovered
                                    ToolTip.text: "90° CCW"
                                    onClicked: { win.quarterTurns = (win.quarterTurns + 3) % 4; win.applyCropAspect() }
                                }
                                Button {
                                    text: "⟳ 90°"
                                    Layout.fillWidth: true
                                    ToolTip.visible: hovered
                                    ToolTip.text: "90° CW"
                                    onClicked: { win.quarterTurns = (win.quarterTurns + 1) % 4; win.applyCropAspect() }
                                }
                            }

                            Label { text: "Flip"; color: "white"; font.pixelSize: 12 }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                Button {
                                    id: flipHBtn
                                    text: "Flip horizontal"
                                    checkable: true
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 0
                                }
                                Button {
                                    id: flipVBtn
                                    text: "Flip vertical"
                                    checkable: true
                                    Layout.fillWidth: true
                                    Layout.preferredWidth: 0
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Geometry (원근/왜곡 보정) ----
                            Label {
                                text: "Geometry"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label { text: "Vertical perspective:  " + geoVSlider.value.toFixed(0); color: "white" }
                            Slider {
                                id: geoVSlider
                                Layout.fillWidth: true
                                from: -100; to: 100; value: 0
                                property real defaultValue: 0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoVSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }
                            Label { text: "Horizontal perspective:  " + geoHSlider.value.toFixed(0); color: "white" }
                            Slider {
                                id: geoHSlider
                                Layout.fillWidth: true
                                from: -100; to: 100; value: 0
                                property real defaultValue: 0
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoHSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }
                            Label { text: "Scale:  " + geoScaleSlider.value.toFixed(0) + "%"; color: "white" }
                            Slider {
                                id: geoScaleSlider
                                Layout.fillWidth: true
                                from: 50; to: 150; value: 100
                                property real defaultValue: 100
                                property real _lastPressMs: 0
                                property bool _pendingReset: false
                                onPressedChanged: {
                                    if (pressed) _pendingReset = win.isDblPress(geoScaleSlider)
                                    else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            Button {
                                text: "Reset Crop · Rotate · Geometry"
                                Layout.fillWidth: true
                                onClicked: win.resetGeometry()
                            }

                            Label {
                                Layout.fillWidth: true
                                wrapMode: Text.WordWrap
                                text: "Note: crop · rotate · geometry (vertical/horizontal perspective · scale) all apply to both preview and export. Trim the empty areas left after perspective correction with the crop tool."
                                color: "#888"; font.pixelSize: 11
                            }
                        }
                    }

                    // ===== index 2: Masking (영역별 로컬 조정 — ML 세그 마스크) =====
                    Flickable {
                        id: maskScroll
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        contentWidth: width
                        contentHeight: maskCol.height + 32
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: B.ScrollBar {
                            id: maskBar
                            width: 12
                            policy: ScrollBar.AlwaysOn
                            contentItem: Rectangle { implicitWidth: 8; radius: 4; color: maskBar.pressed ? "#cfcfcf" : "#9a9a9a" }
                            background: Rectangle { radius: 4; color: "#3a3a3a" }
                        }

                        ColumnLayout {
                            id: maskCol
                            x: 16; y: 16
                            width: maskScroll.width - 32
                            spacing: 12

                            // ---- Create Mask: 클래스 체크박스(복합 선택, 라이브 재조합) ----
                            Label {
                                text: "Create Mask"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Check one or more — the mask is the union of the selected classes."
                                color: "#888"; font.pixelSize: 11
                            }
                            GridLayout {
                                Layout.fillWidth: true
                                columns: 2
                                columnSpacing: 4; rowSpacing: 2
                                Repeater {
                                    model: controller.maskGroups
                                    delegate: RowLayout {
                                        Layout.fillWidth: true; spacing: 6
                                        CheckBox {
                                            checked: win.maskKeys.indexOf(modelData.key) >= 0
                                            enabled: controller.imagePath !== "" && !controller.skyBusy
                                            onToggled: win.toggleMaskKey(modelData.key, checked)
                                        }
                                        Label {
                                            Layout.fillWidth: true; text: modelData.label
                                            color: "white"; font.pixelSize: 12
                                            verticalAlignment: Text.AlignVCenter
                                        }
                                    }
                                }
                            }
                            Button {
                                text: "Clear"
                                Layout.fillWidth: true
                                enabled: controller.imagePath !== ""
                                onClicked: win.resetSky()
                            }
                            // 선택 진행 중/완료 상태는 이미지 위 스피너 오버레이가 표시(controller.skyBusy).
                            // 선택 '완료'(클리어 제외) → 마스크 오버레이 자동 표시
                            Connections {
                                target: controller
                                // 사용자 선택 → 오버레이 자동 표시. 단 사이드카 복원 재생성은 제외.
                                function onSkySelected() {
                                    if (win._maskRestore) win._maskRestore = false
                                    else win.showSkyMask = true
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                CheckBox {
                                    id: skyShowCheck
                                    checked: win.showSkyMask
                                    onToggled: win.showSkyMask = checked
                                }
                                Label {
                                    Layout.fillWidth: true; text: "Show mask overlay (red)"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 6
                                CheckBox { id: skyInvertCheck }
                                Label {
                                    Layout.fillWidth: true; text: "Invert mask (everything but the selection)"
                                    color: "white"; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter
                                }
                            }

                            Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                            // ---- Adjustments (활성 마스크 영역 전용) ----
                            Label {
                                text: "Adjustments"
                                color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                                font.capitalization: Font.AllUppercase
                            }
                            SkySlider { id: skyExpSlider;     host: win; label: "Exposure"; suffix: "  (stop)" }
                            SkySlider { id: skyTempSlider;    host: win; label: "Temp"; suffix: "  (− cool / + warm)" }
                            SkySlider { id: skyTintSlider;    host: win; label: "Tint"; suffix: "  (− green / + magenta)" }
                            SkySlider { id: skyHiSlider;      host: win; label: "Highlights" }
                            SkySlider { id: skyShadowsSlider; host: win; label: "Shadows" }
                            SkySlider { id: skyTextureSlider; host: win; label: "Texture" }
                            SkySlider { id: skyClaritySlider; host: win; label: "Clarity" }
                            SkySlider { id: skyDehazeSlider;  host: win; label: "Dehaze" }
                            SkySlider { id: skySatSlider;     host: win; label: "Saturation" }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Check one or more classes above to build the mask; the sliders apply only to the masked region. Applies to both preview and export."
                                color: "#888"; font.pixelSize: 11
                            }
                        }
                    }
                }   // end StackLayout
            }       // end 우측 패널 outer ColumnLayout
        }           // end 우측 패널 Rectangle

        // ---------- 우측 끝 세로 패널 셀렉터 바 ----------
        Rectangle {
            Layout.preferredWidth: 44
            Layout.fillHeight: true
            color: "#222"

            Column {
                anchors.top: parent.top
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.topMargin: 8
                spacing: 4

                Repeater {
                    model: [
                        { icon: "edit", tip: "Edit", key: "Ctrl+1" },
                        { icon: "crop", tip: "Crop / Rotate / Geometry", key: "Ctrl+2" },
                        { icon: "mask", tip: "Masking", key: "Ctrl+3" }
                    ]
                    delegate: Rectangle {
                        width: 40; height: 40
                        radius: 6
                        color: win.activePanel === index ? "#3a4a6b"
                               : (selMouse.containsMouse ? "#33373f" : "transparent")
                        border.width: win.activePanel === index ? 1 : 0
                        border.color: "#8ab4f8"

                        // 기능 아이콘(편집=연필, 크롭=크롭 브래킷). 활성=accent, 비활성=회색.
                        Canvas {
                            anchors.fill: parent
                            property string ic: modelData.icon
                            property color col: win.activePanel === index ? "#8ab4f8"
                                                : (selMouse.containsMouse ? "#e6e6e6" : "#cfcfcf")
                            onColChanged: requestPaint()
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var o = 8                       // 40px 버튼 안 24px 아이콘 오프셋
                                function P(x, y) { return [o + x, o + y] }
                                ctx.lineWidth = 2
                                ctx.lineJoin = "round"; ctx.lineCap = "round"
                                ctx.strokeStyle = col; ctx.fillStyle = col
                                if (ic === "edit") {
                                    // 조정 슬라이더 아이콘(가로선 3 + 노브) — 사진 보정 패널
                                    var rows = [[6, 16], [12, 9], [18, 14]]   // [y, knobX]
                                    for (var i = 0; i < 3; i++) {
                                        var a = P(3, rows[i][0]), b = P(21, rows[i][0])
                                        ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke()
                                        var k = P(rows[i][1], rows[i][0])
                                        ctx.beginPath(); ctx.arc(k[0], k[1], 2.6, 0, 2 * Math.PI); ctx.fill()
                                    }
                                } else if (ic === "crop") {
                                    // 크롭 브래킷(└ 좌하 + ┐ 우상)
                                    ctx.lineCap = "butt"; ctx.lineJoin = "miter"
                                    function seg(a, b) { ctx.beginPath(); ctx.moveTo(a[0],a[1]); ctx.lineTo(b[0],b[1]); ctx.stroke() }
                                    seg(P(7,2), P(7,17));  seg(P(7,17), P(22,17))
                                    seg(P(2,7), P(17,7));  seg(P(17,7), P(17,22))
                                } else {
                                    // 마스킹: 프레임(이미지) + 채운 원(선택 영역) — 영역별 보정
                                    ctx.strokeRect(o + 3, o + 4, 18, 16)
                                    var c = P(9, 13)
                                    ctx.beginPath(); ctx.arc(c[0], c[1], 5, 0, 2 * Math.PI); ctx.fill()
                                }
                            }
                        }
                        MouseArea {
                            id: selMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: win.activePanel = index
                        }
                        ToolTip.visible: selMouse.containsMouse
                        ToolTip.delay: 1500
                        ToolTip.text: modelData.tip + "  (" + modelData.key + ")"
                    }
                }
            }
        }
    }

}

import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: win
    visible: true
    width: 1280
    height: 820
    title: "RAW Editor — skeleton"
    color: "#1a1a1a"

    // === WB 실시간 프리뷰 (드래그 중) ===
    // baked 색온도로 디코딩된 프록시에 "baked->target" 상대 게인만 셰이더로 입힌다.
    // 손을 떼면 target 색온도로 재디코딩(확정)하고 게인은 (1,1,1) 로 수렴 -> 이중적용 없음.
    // 유도상 daylight_ref·기준온도가 약분돼 카메라 매트릭스(camMatrix)만 있으면 계산 가능.
    readonly property int wbTRef: 5500

    // 콤보 인덱스 -> luts/<key>.cube 파일명. 0(identity)=필름시뮬 미적용.
    readonly property var simKeys: [
        "identity", "provia", "velvia", "astia",
        "classic_chrome", "classic_neg", "nostalgic_neg",
        "pro_neg_hi", "pro_neg_std", "eterna",
        "reala_ace", "bleach_bypass"
    ]

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

    // 새 파일 로드 시 추정된 as-shot 색온도로 Temp 슬라이더 초기화.
    Connections {
        target: controller
        function onAsShotKelvinChanged() {
            tempSlider.value = controller.asShotKelvin
            tintSlider.value = 0.0
        }
    }

    FileDialog {
        id: fileDialog
        title: "RAF 파일 열기"
        nameFilters: ["Fuji RAW (*.raf *.RAF)", "All files (*)"]
        onAccepted: controller.load(selectedFile)
    }

    FileDialog {
        id: saveDialog
        title: "내보내기 (풀해상도)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["JPEG (*.jpg)", "PNG (*.png)", "TIFF (*.tif)"]
        defaultSuffix: "jpg"
        onAccepted: controller.exportImage(selectedFile, {
            "exposure": expSlider.value,
            "contrast": conSlider.value,
            "highlights": hiSlider.value,
            "shadows": shSlider.value,
            "whites": whSlider.value,
            "blacks": blSlider.value,
            "texAmt": texSlider.value,
            "clarity": claritySlider.value,
            "dehaze": dehazeSlider.value,
            "vignette": vignetteSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0,
            "simKey": win.simKeys[simCombo.currentIndex],
            "curve": curveEditor.lut256()
        })
    }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ---------- 이미지 영역 ----------
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#1e1e1e"

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

            // 톤 커브 1D LUT 텍스처 (256x1). 보간 위해 smooth:true.
            Image {
                id: curveImage
                visible: false
                cache: false
                smooth: true
                source: controller.curveUrl
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
                              : "열린 파일 없음"
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
                    property real ar: procW / procH
                    property real fitW: Math.min(availW, availH * ar)
                    property real fitH: fitW / ar
                    property real claW: Math.max(1, Math.round(procW / 4))   // 클래리티 블러 다운샘플
                    property real claH: Math.max(1, Math.round(procH / 4))

                    // --- 로컬대비용 가우시안 블러 (src 에만 의존 -> 로드 시 1회 계산) ---
                    // 텍스처: 작은 반경, 풀 프록시 해상도
                    ShaderEffect {
                        id: texBlurH; visible: false
                        width: viewport.procW; height: viewport.procH
                        property variant src: srcImage
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
                        property variant src: srcImage
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

                    // 파이프라인 셰이더: 프록시 해상도에서만 렌더(직접 표시 안 함)
                    ShaderEffect {
                        id: pipe
                        width: viewport.procW
                        height: viewport.procH
                        visible: false

                        // 셰이더 uniform 과 이름이 일치해야 함
                        property variant src: srcImage
                        property variant lut: lutImage
                        property variant curve: curveImage
                        property variant texBlur: texBlurTex
                        property variant claBlur: claBlurTex
                        property real exposure: expSlider.value
                        property real contrast: conSlider.value
                        property real highlights: hiSlider.value
                        property real shadows: shSlider.value
                        property real whites: whSlider.value
                        property real blacks: blSlider.value
                        property real texAmt: texSlider.value
                        property real clarity: claritySlider.value
                        property real dehaze: dehazeSlider.value
                        property real vignette: vignetteSlider.value
                        // WB 실시간 프리뷰 게인 (baked->target). 커밋되면 (1,1,1).
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN             // context property (LUT 크기 N)
                        property int lutEnabled: simCombo.currentIndex === 0 ? 0 : 1

                        fragmentShader: "shaders/adjust.frag.qsb"
                    }

                    // 고정 크기 FBO(프록시 해상도)에 렌더한 뒤 화면 크기로 스케일 표시.
                    // -> 프래그먼트 연산량이 모니터 해상도에 비례하지 않음.
                    ShaderEffectSource {
                        id: pipeView
                        visible: srcImage.status === Image.Ready
                        sourceItem: pipe
                        textureSize: Qt.size(viewport.procW, viewport.procH)
                        width: viewport.fitW
                        height: viewport.fitH
                        anchors.centerIn: parent
                        hideSource: true
                        smooth: true
                        live: true
                    }

                    Text {
                        visible: srcImage.status !== Image.Ready
                        anchors.centerIn: parent
                        color: "#888"
                        font.pixelSize: 16
                        text: "오른쪽 'Open RAF…' 버튼으로 파일을 여세요"
                    }
                }
            }
        }

        // ---------- 우측 패널 (스크롤) ----------
        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: "#2b2b2b"

            ScrollView {
                id: panelScroll
                anchors.fill: parent
                padding: 16
                clip: true

                ColumnLayout {
                    width: panelScroll.availableWidth
                    spacing: 12

                Button {
                    text: "Open RAF…"
                    Layout.fillWidth: true
                    onClicked: fileDialog.open()
                }

                Button {
                    text: "Export (full-res)…"
                    Layout.fillWidth: true
                    enabled: controller.imagePath !== ""
                    onClicked: saveDialog.open()
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

                // 필름 시뮬레이션 선택
                Label { text: "Film Simulation"; color: "white" }
                ComboBox {
                    id: simCombo
                    Layout.fillWidth: true
                    currentIndex: 0
                    // 인덱스 순서가 셰이더 film_sim() 분기와 일치해야 함
                    // 순서가 위 simKeys 와 정확히 일치해야 함
                    model: [
                        "None",
                        "Provia / Standard",
                        "Velvia",
                        "Astia",
                        "Classic Chrome",
                        "Classic Negative",
                        "Nostalgic Neg",
                        "PRO Neg. Hi",
                        "PRO Neg. Std",
                        "Eterna",
                        "Reala Ace",
                        "Bleach Bypass"
                    ]
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Exposure:  " + expSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: expSlider
                    Layout.fillWidth: true
                    from: -3.0; to: 3.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(expSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Contrast:  " + conSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: conSlider
                    Layout.fillWidth: true
                    from: 0.5; to: 2.0; value: 1.0
                    property real defaultValue: 1.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(conSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Highlights:  " + hiSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: hiSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(hiSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Shadows:  " + shSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: shSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(shSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Whites:  " + whSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: whSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(whSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Label {
                    text: "Blacks:  " + blSlider.value.toFixed(2)
                    color: "white"
                }
                Slider {
                    id: blSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
                    property real defaultValue: 0.0
                    property real _lastPressMs: 0
                    property bool _pendingReset: false
                    onPressedChanged: {
                        if (pressed) _pendingReset = win.isDblPress(blSlider)
                        else if (_pendingReset) { value = defaultValue; _pendingReset = false }
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

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
                    text: "Vignette:  " + vignetteSlider.value.toFixed(2) + "  (− 어둡게)"
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

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

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
                    onValueChanged: if (!pressed) wbTimer.restart()
                }

                Label {
                    text: "Tint:  " + tintSlider.value.toFixed(2) + "  (− green / + magenta)"
                    color: "white"
                }
                Slider {
                    id: tintSlider
                    Layout.fillWidth: true
                    from: -1.0; to: 1.0; value: 0.0
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
                    onValueChanged: if (!pressed) wbTimer.restart()
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label { text: "Tone Curve"; color: "white" }
                CurveEditor {
                    id: curveEditor
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240     // 고정 높이(너비에서 분리: 레이아웃 루프 방지)
                    onEdited: controller.setCurve(lut256())
                }

                Button {
                    text: "Reset"
                    Layout.fillWidth: true
                    onClicked: {
                        expSlider.value = 0.0
                        conSlider.value = 1.0
                        hiSlider.value = 0.0
                        shSlider.value = 0.0
                        whSlider.value = 0.0
                        blSlider.value = 0.0
                        texSlider.value = 0.0
                        claritySlider.value = 0.0
                        dehazeSlider.value = 0.0
                        vignetteSlider.value = 0.0
                        tempSlider.value = controller.asShotKelvin
                        tintSlider.value = 0.0
                        simCombo.currentIndex = 0
                        curveEditor.reset()
                    }
                }
                }
            }
        }
    }
}

import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
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

    // 촬영정보 플로팅 패널 표시 여부 (I 키로 토글)
    property bool infoOverlay: true
    Shortcut { sequence: "I"; onActivated: win.infoOverlay = !win.infoOverlay }

    // 날짜 스탬프(필름 데이트백) 표시 여부 (D 키로 토글). 기본 off.
    property bool dateStamp: false
    Shortcut { sequence: "D"; onActivated: win.dateStamp = !win.dateStamp }

    // 좌측 File Explorer 패널 표시 여부 (B 키로 토글)
    property bool showExplorer: true
    Shortcut { sequence: "B"; onActivated: win.showExplorer = !win.showExplorer }

    // 탐색기 "좋아요만 보기" 필터
    property bool showLikedOnly: false
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

    // 카메라 네이티브 -> 선형 sRGB 매트릭스(행우선 9개). 로드 전엔 identity.
    readonly property var camM: (controller.camToSrgb && controller.camToSrgb.length >= 9)
                                ? controller.camToSrgb : [1,0,0, 0,1,0, 0,0,1]
    // dispSrc(블러 base)용 as-shot WB 상대게인(TREF 대비). bakedKelvin=TREF 이므로
    // wbPreview(asShot,0) = userWb(asShot)/userWb(TREF) 가 된다.
    readonly property vector3d asShotRelGain: win.wbPreview(controller.asShotKelvin, 0)

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
            "curve": curveEditor.lut256()
        }
    }

    // 새 파일 로드 시 추정된 as-shot 색온도로 Temp 슬라이더 초기화.
    Connections {
        target: controller
        function onAsShotKelvinChanged() {
            tempSlider.value = controller.asShotKelvin
            tintSlider.value = 0.0
        }
        // 로드/WB 커밋(재디코딩)으로 프록시가 갱신되면 조절 반영 히스토그램 재계산.
        function onImageChanged() { win.refreshHistogram() }
    }

    FolderDialog {
        id: folderDialog
        title: "폴더 선택"
        onAccepted: controller.setFolder(selectedFolder)   // QUrl -> Python .toLocalFile()
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
        title: "내보내기 (풀해상도)"
        fileMode: FileDialog.SaveFile
        nameFilters: ["PNG (*.png)", "JPEG (*.jpg)", "TIFF (*.tif)"]
        defaultSuffix: "png"
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
            "saturation": satSlider.value,
            "vibrance": vibSlider.value,
            "vignette": vignetteSlider.value,
            "grainAmt": grainSlider.value,
            "grainSize": grainSizeSlider.value,
            "lutEnabled": simCombo.currentIndex !== 0,
            "simKey": win.simKeys[simCombo.currentIndex],
            "lutStrength": simStrengthSlider.value,
            "curve": curveEditor.lut256(),
            "dateStamp": win.dateStamp,
            "stampText": stampField.text,
            "outEdge": win.exportEdges[resCombo.currentIndex],
            "lensCorrection": lensCheck.checked
        })
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
                        ToolTip.text: "상위 폴더"
                        onClicked: controller.goUp()
                    }
                    Button {
                        id: folderBtn
                        text: "폴더…"
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
                        ToolTip.text: "좋아요만 보기"

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
                    text: controller.currentFolder || "폴더를 선택하세요"
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
                                    color: "#e6e6e6"
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
            ToolTip.text: (win.showExplorer ? "탐색기 숨기기" : "탐색기 보이기") + " (B)"
        }

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

            // 날짜 스탬프 오버레이 텍스처(프록시 RGBA). 셰이더가 가산 합성.
            Image {
                id: stampImage
                visible: false
                cache: false
                smooth: true
                source: controller.stampUrl
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
                        property variant stampTex: stampImage
                        property real camM0: win.camM[0]; property real camM1: win.camM[1]; property real camM2: win.camM[2]
                        property real camM3: win.camM[3]; property real camM4: win.camM[4]; property real camM5: win.camM[5]
                        property real camM6: win.camM[6]; property real camM7: win.camM[7]; property real camM8: win.camM[8]
                        property real stampOn: (win.dateStamp && controller.stampText !== "") ? 1.0 : 0.0
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
                        property real vignette: vignetteSlider.value
                        property real grainAmt: grainSlider.value
                        property real grainSize: grainSizeSlider.value
                        property real grainAspect: viewport.procW / Math.max(1, viewport.procH)
                        // WB 게인: TREF 베이크 대비 상대게인(카메라공간). 재디코딩 없이 실시간.
                        property vector3d wbGain: win.wbPreview(tempSlider.value, tintSlider.value)
                        property real wbR: wbGain.x
                        property real wbG: wbGain.y
                        property real wbB: wbGain.z
                        property real lutSize: lutN             // context property (LUT 크기 N)
                        property real lutStrength: simStrengthSlider.value
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
                        text: "왼쪽 탐색기에서 RAF 파일을 더블클릭해 여세요"
                    }

                    // 촬영정보 플로팅 패널 (I 키 토글) — 좌측 뷰 왼쪽 끝에 고정
                    Rectangle {
                        visible: win.infoOverlay && pipeView.visible
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
                                text: "Shooting Info"
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

            // 진행 중 스피너 오버레이 (이미지 위): export 또는 디코딩(렌즈 보정 등)
            Rectangle {
                anchors.fill: parent
                visible: controller.exporting || controller.busy
                color: "#aa000000"
                MouseArea { anchors.fill: parent }   // 진행 중 이미지 입력 차단
                ColumnLayout {
                    anchors.centerIn: parent
                    spacing: 12
                    BusyIndicator {
                        running: controller.exporting || controller.busy
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 64; implicitHeight: 64
                    }
                    Label {
                        text: controller.exporting ? "내보내는 중…" : "처리 중…"
                        color: "white"; font.pixelSize: 14
                        Layout.alignment: Qt.AlignHCenter
                    }
                }
            }
        }

        // ---------- 우측 패널 (스크롤) ----------
        Rectangle {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            color: "#2b2b2b"

            Flickable {
                id: panelScroll
                anchors.fill: parent
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

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Button {
                        text: "Export…"
                        Layout.fillWidth: true
                        enabled: controller.imagePath !== ""
                        onClicked: saveDialog.open()
                    }
                    Button {
                        id: resetBtn
                        text: "↺"                       // Reset 아이콘(조절 초기화)
                        Layout.preferredWidth: 26
                        Layout.preferredHeight: 26       // 작은 정사각
                        Layout.alignment: Qt.AlignVCenter
                        padding: 0
                        font.pixelSize: 14
                        ToolTip.visible: hovered
                        ToolTip.text: "Reset (조절 초기화)"
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
                            satSlider.value = 0.0
                            vibSlider.value = 0.0
                            vignetteSlider.value = 0.0
                            grainSlider.value = 0.0
                            grainSizeSlider.value = 0.5
                            tempSlider.value = controller.asShotKelvin
                            tintSlider.value = 0.0
                            simCombo.currentIndex = 0
                            simStrengthSlider.value = 1.0
                            curveEditor.reset()
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "해상도"; color: "white"; font.pixelSize: 12 }
                    ComboBox {
                        id: resCombo
                        Layout.fillWidth: true
                        currentIndex: 0     // 원본
                        model: ["원본 (Full)", "4096", "3840 (4K)",
                                "2560", "2048", "1920 (FHD)", "1280"]
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

                // 필름 시뮬레이션 선택
                Label {
                    text: "Film Simulation"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
                ComboBox {
                    id: simCombo
                    Layout.fillWidth: true
                    currentIndex: 0
                    onActivated: win.refreshHistogram()
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

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Light"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }

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

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Tone Curve"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
                CurveEditor {
                    id: curveEditor
                    Layout.fillWidth: true
                    Layout.preferredHeight: 240     // 고정 높이(너비에서 분리: 레이아웃 루프 방지)
                    histogram: controller.histogram
                    onEdited: { controller.setCurve(lut256()); win.refreshHistogram() }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "White Balance"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }

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

                Label {
                    text: "Lens Corrections"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
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
                        text: "X100V 프로파일 (왜곡·주변광량·CA)"
                        color: "white"; font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Color"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
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

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Detail & Vignette"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
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
                    text: "Grain"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
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
                    text: "Grain Size:  " + grainSizeSlider.value.toFixed(2) + "  (작게 ↔ 굵게)"
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

                Rectangle { Layout.fillWidth: true; height: 1; color: "#444" }

                Label {
                    text: "Date Stamp"
                    color: "#8ab4f8"; font.pixelSize: 12; font.bold: true
                    font.capitalization: Font.AllUppercase
                }
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
                        text: "필름 날짜 각인  — D"
                        color: stampCheck.enabled ? "white" : "#777"
                        font.pixelSize: 12
                        verticalAlignment: Text.AlignVCenter
                    }
                }
                // 날짜 직접 입력(기본값=EXIF). 변경 시 디바운스 후 프리뷰 재렌더.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Label { text: "날짜"; color: "white"; font.pixelSize: 12 }
                    TextField {
                        id: stampField
                        Layout.fillWidth: true
                        enabled: win.dateStamp && controller.imagePath !== ""
                        placeholderText: "'YY MM DD  (예: '24 05 12)"
                        onTextEdited: stampDebounce.restart()
                    }
                }
                Timer {
                    id: stampDebounce
                    interval: 200
                    onTriggered: controller.setStampText(stampField.text)
                }
                Connections {
                    target: controller
                    // 새 파일 로드 시 입력필드를 EXIF 날짜로 동기화(사용자 편집은 안 건드림)
                    function onStampReset() { stampField.text = controller.stampText }
                }
                }
            }
        }
    }
}

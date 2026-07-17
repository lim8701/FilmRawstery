import QtQuick
import QtQuick.Controls.Basic as B

// 파일 탐색기 "프리뷰 모드" — 별도 OS 창이 아니라 메인 창 위를 꽉 덮는 인앱 오버레이.
// 이미 살아있는 씬그래프를 재사용하므로 첫 오픈이 좌/우 이동만큼 즉시 뜬다(창 생성 비용 없음).
//  - RAW 내장 풀 프리뷰(image://preview, ~2048px)를 윈도우 기본 이미지 뷰어처럼 크게 표시
//  - 좌/우 화살표(버튼·키보드)로 이전/다음 사진 이동(양 끝에서 멈춤)
//  - 하단 하트로 좋아요(셀렉트) 토글 -> controller.toggleLike (즉시 폴더 JSON 저장)
//  - ✕ 버튼 또는 ESC 로 닫기
Item {
    id: previewWin
    anchors.fill: parent          // 메인 창 contentItem 을 꽉 채움
    visible: false
    z: 1000

    // Main.qml 에서 채워줌
    property var rawList: []          // 사진 경로 배열(폴더 내 RAW, 디렉터리 제외)
    property int idx: 0
    readonly property string currentPath:
        (idx >= 0 && idx < rawList.length) ? rawList[idx] : ""
    readonly property string currentName: {
        var p = currentPath
        if (!p) return ""
        var s = p.replace(/\\/g, "/")
        return s.substring(s.lastIndexOf("/") + 1)
    }
    // likeRevision 을 참조해 토글/폴더변경 시 자동 재평가
    readonly property bool liked: {
        controller.likeRevision
        return currentPath !== "" && controller.isLiked(currentPath)
    }

    // 닫힐 때 마지막으로 보던 사진 경로 전달 → Main 이 탐색기에서 선택(로드 아님)로 이어감.
    signal closedAt(string path)

    function open(list, startIdx) {
        rawList = list
        idx = startIdx
        visible = true
        keyScope.forceActiveFocus()
    }
    function close() {
        visible = false
        closedAt(currentPath)
    }
    function prev() { if (idx > 0) idx-- }
    function next() { if (idx < rawList.length - 1) idx++ }

    // 키보드 입력 수신 + 뒤(탐색기)로 클릭 통과 차단
    Item {
        id: keyScope
        anchors.fill: parent
        focus: previewWin.visible
        Keys.onLeftPressed: previewWin.prev()
        Keys.onRightPressed: previewWin.next()
        Keys.onEscapePressed: previewWin.close()
        Keys.onSpacePressed: {
            if (previewWin.currentPath)
                controller.toggleLike(previewWin.currentPath)
        }

        // ---------- 배경(불투명) — 뒤 클릭 차단 ----------
        Rectangle {
            anchors.fill: parent
            color: "#101010"
            MouseArea { anchors.fill: parent }   // 클릭이 탐색기로 새지 않게 흡수
            Text {
                anchors.centerIn: parent
                visible: bigImg.status !== Image.Ready
                text: bigImg.status === Image.Loading ? "Loading…" : "No preview"
                color: "#777"
                font.pixelSize: 16
            }
        }

        // ---------- 중앙 이미지 ----------
        Image {
            id: bigImg
            anchors.fill: parent
            anchors.margins: 24
            anchors.bottomMargin: 84      // 하단 컨트롤 영역 비움
            fillMode: Image.PreserveAspectFit
            asynchronous: true
            cache: false                  // PreviewProvider 가 자체 LRU 캐시 보유
            sourceSize.width: 2048
            source: (previewWin.visible && previewWin.currentPath)
                    ? "image://preview/" + encodeURIComponent(previewWin.currentPath)
                      + "?i=" + previewWin.idx
                    : ""
        }

        // ---------- 공통 원형 버튼(어두운 반투명 + Canvas 로 그린 화살표) ----------
        // 폰트 글리프(‹ › ←)가 세로로 치우쳐 Text 로는 중앙정렬이 안 맞으므로
        // 화살표를 Canvas 로 직접 그려 기하학적으로 정확히 중앙에 배치한다.
        component NavButton: Rectangle {
            id: nav
            property string kind: "left"      // "left" | "right" | "back"
            signal clicked()
            width: 56; height: 56
            radius: width / 2
            color: navMa.pressed ? "#5a5a5a" : (navHov.hovered ? "#484848" : "#333333")
            opacity: nav.enabled ? 0.85 : 0.3   // Item opacity 는 자식(화살표)까지 함께 흐려짐
            border.color: "#666666"
            border.width: 1

            Canvas {
                id: arrowCv
                anchors.fill: parent
                onPaint: {
                    var ctx = getContext("2d")
                    ctx.reset()
                    ctx.strokeStyle = "#f0f0f0"
                    ctx.lineWidth = Math.max(2, width * 0.07)
                    ctx.lineCap = "round"
                    ctx.lineJoin = "round"
                    var cx = width / 2, cy = height / 2
                    var s = width * 0.18
                    if (nav.kind === "back") {
                        // 화살표(←): 꼬리선 + 좌측 화살촉. 바운딩박스를 cx 기준 좌우 대칭으로.
                        var W = s * 0.9, hw = s * 0.5, hh = s * 0.55
                        ctx.beginPath()
                        ctx.moveTo(cx - W, cy)
                        ctx.lineTo(cx + W, cy)
                        ctx.stroke()
                        ctx.beginPath()
                        ctx.moveTo(cx - W + hw, cy - hh)
                        ctx.lineTo(cx - W, cy)
                        ctx.lineTo(cx - W + hw, cy + hh)
                        ctx.stroke()
                    } else if (nav.kind === "right") {
                        // 오른쪽 셰브런: 잉크 무게중심이 cx 에 오도록(꼭짓점 +0.6s, 양팔 -0.3s)
                        ctx.beginPath()
                        ctx.moveTo(cx - s * 0.3, cy - s)
                        ctx.lineTo(cx + s * 0.6, cy)
                        ctx.lineTo(cx - s * 0.3, cy + s)
                        ctx.stroke()
                    } else {                          // left
                        ctx.beginPath()
                        ctx.moveTo(cx + s * 0.3, cy - s)
                        ctx.lineTo(cx - s * 0.6, cy)
                        ctx.lineTo(cx + s * 0.3, cy + s)
                        ctx.stroke()
                    }
                }
                Component.onCompleted: requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
            }
            HoverHandler { id: navHov }
            MouseArea {
                id: navMa
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: nav.clicked()
            }
        }

        // ---------- 좌/우 네비게이션 버튼 ----------
        NavButton {
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            kind: "left"
            enabled: previewWin.idx > 0
            onClicked: previewWin.prev()
        }
        NavButton {
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            kind: "right"
            enabled: previewWin.idx < previewWin.rawList.length - 1
            onClicked: previewWin.next()
        }

        // ---------- 좌상단 이전(←) — 프리뷰 닫고 탐색기로 복귀 ----------
        NavButton {
            id: backBtn
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.margins: 12
            width: 44; height: 44
            kind: "back"
            onClicked: previewWin.close()
        }

        // ---------- 프리뷰 모드 표시 배지(← 버튼 옆) ----------
        Rectangle {
            anchors.left: backBtn.right
            anchors.leftMargin: 10
            anchors.verticalCenter: backBtn.verticalCenter
            height: 26
            width: badgeRow.implicitWidth + 20
            radius: 13
            color: "#1e2a3a"
            opacity: 0.92
            border.color: "#8ab4f8"
            border.width: 1

            Row {
                id: badgeRow
                anchors.centerIn: parent
                spacing: 6
                Rectangle {
                    width: 7; height: 7; radius: 3.5
                    color: "#8ab4f8"
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: "PREVIEW MODE"
                    color: "#8ab4f8"
                    font.pixelSize: 12
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }

        // ---------- 상단 파일명 / 인덱스 ----------
        Text {
            anchors.top: parent.top
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.topMargin: 16
            color: "#e6e6e6"
            font.pixelSize: 14
            text: previewWin.currentName
                  + "    " + (previewWin.idx + 1) + " / " + previewWin.rawList.length
        }

        // ---------- 하단 하트(좋아요) ----------
        Row {
            anchors.bottom: parent.bottom
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottomMargin: 24
            spacing: 8

            Text {
                id: heart
                anchors.verticalCenter: parent.verticalCenter
                text: previewWin.liked ? "♥" : "♡"
                color: previewWin.liked ? "#ff6b6b" : "#cfcfcf"
                font.pixelSize: 34

                MouseArea {
                    anchors.fill: parent
                    anchors.margins: -10      // 클릭 영역 살짝 넓힘
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        if (previewWin.currentPath)
                            controller.toggleLike(previewWin.currentPath)
                    }
                }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: previewWin.liked ? "Liked" : "Space / click to like"
                color: "#9a9a9a"
                font.pixelSize: 12
            }
        }
    }
}

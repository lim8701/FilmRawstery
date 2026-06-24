import QtQuick

// 콜드 스타트(프로세스 시작 ~ 메인 창 첫 프레임) 동안 보이는 스플래시.
// ⚠️ QtQuick.Controls 를 임포트하지 않는다: splash 가 메인 창보다 먼저 로드되는데
//    Controls 스타일은 "런타임에서 처음 만난 Controls 임포트"가 앱 전체 스타일을
//    결정한다. 여기서 Controls 를 쓰면 앱 기본 스타일이 바뀌어 메인 창 컨트롤(버튼 등)
//    크기가 달라진다. 그래서 순수 QtQuick(Rectangle/Text/Row/Repeater)만 사용.
//    종료 다이얼로그와 같은 컨셉(다크 패널 + 앰버 필름 퍼포레이션 + 동일 타이포).
Rectangle {
    width: 460
    height: 250
    color: "#232325"
    radius: 16
    border.color: "#3d3d40"
    border.width: 1

    // 상단 필름 퍼포레이션 스트립(앰버)
    Row {
        anchors.top: parent.top
        anchors.topMargin: 20
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 9
        Repeater {
            model: 13
            Rectangle { width: 16; height: 10; radius: 2; color: "#E0A226" }
        }
    }

    // 하단 필름 퍼포레이션 스트립(앰버) — 필름 프레임처럼 위아래 대칭
    Row {
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 20
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 9
        Repeater {
            model: 13
            Rectangle { width: 16; height: 10; radius: 2; color: "#E0A226" }
        }
    }

    Column {
        anchors.centerIn: parent
        spacing: 14

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Film Rawstery"
            color: "#f2f2f2"
            font.pixelSize: 32
            font.weight: Font.DemiBold
            font.letterSpacing: 1.5
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Film Simulation RAW Editor"
            color: "#9a9a9a"
            font.pixelSize: 13
            font.letterSpacing: 0.5
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "starting…"
            color: "#6a6a6a"
            font.pixelSize: 12
            font.letterSpacing: 2
            topPadding: 6
        }
    }
}

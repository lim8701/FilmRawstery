import QtQuick
import QtQuick.Controls.Basic

// 콜드 스타트(프로세스 시작 ~ 메인 창 첫 프레임) 동안 보이는 가벼운 스플래시.
// QQuickView 로 띄우며, 무거운 내용 없이 즉시 그려지도록 최소 구성만 둔다.
Rectangle {
    width: 440
    height: 240
    color: "#1a1a1a"
    border.color: "#3a3a3a"
    border.width: 1

    Column {
        anchors.centerIn: parent
        spacing: 18

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Film Rawstery"
            color: "#f0f0f0"
            font.pixelSize: 30
            font.weight: Font.DemiBold
            font.letterSpacing: 1.5
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "Fuji RAW Developer"
            color: "#8a8a8a"
            font.pixelSize: 13
            font.letterSpacing: 0.5
        }
        BusyIndicator {
            anchors.horizontalCenter: parent.horizontalCenter
            running: true
            implicitWidth: 40
            implicitHeight: 40
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "starting…"
            color: "#6a6a6a"
            font.pixelSize: 12
        }
    }
}

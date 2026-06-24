import QtQuick

// 콜드 스타트(프로세스 시작 ~ 메인 창 첫 프레임) 동안 보이는 가벼운 스플래시.
// ⚠️ QtQuick.Controls 를 임포트하지 않는다: splash 가 메인 창보다 먼저 로드되는데
//    Controls 스타일은 "런타임에서 처음 만난 Controls 임포트"가 앱 전체 스타일을
//    결정한다. 여기서 Controls.Basic 을 쓰면 앱 기본 스타일이 Basic 으로 고정돼
//    메인 창의 네이티브 컨트롤(버튼 등) 크기가 바뀐다. 그래서 순수 QtQuick 만 사용.
Rectangle {
    width: 440
    height: 190
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
            text: "Film Simulation RAW Editor"
            color: "#8a8a8a"
            font.pixelSize: 13
            font.letterSpacing: 0.5
        }

        // 정적 표시줄: 동기 로딩 구간이라 애니메이션은 못 돈다(이벤트 루프 정지).
        // "뜨는 중"만 알리는 정지 텍스트.
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "starting…"
            color: "#8a8a8a"
            font.pixelSize: 13
            font.letterSpacing: 2
        }
    }
}

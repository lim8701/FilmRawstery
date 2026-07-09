import QtQuick

// 앰버 필름 퍼포레이션 스트립 — 주어진 폭을 가득 채우도록 구멍을 균등 분포한다.
// ⚠️ 순수 QtQuick 만 사용(Controls 임포트 금지): Splash 가 메인 창보다 먼저 로드되며
//    Controls 임포트가 앱 전체 스타일을 바꿀 수 있어 splash 계열에선 쓰면 안 됨.
// 종료 대화상자(상/하)와 splash(상/하)가 공유한다.
Item {
    id: root
    property int holeW: 14
    property int holeH: 9
    property int gap: 9
    property color holeColor: "#E0A226"
    implicitHeight: 26
    clip: true

    // 폭에 맞춰 구멍 개수 자동 산출(구멍+간격 피치) → 남는 폭을 간격에 분배해 가장자리까지 채움.
    property int holes: Math.max(2, Math.floor((width + gap) / (holeW + gap)))
    Row {
        anchors.centerIn: parent
        spacing: root.holes > 1
                 ? (root.width - root.holes * root.holeW) / (root.holes - 1)
                 : root.gap
        Repeater {
            model: root.holes
            Rectangle { width: root.holeW; height: root.holeH; radius: 2; color: root.holeColor }
        }
    }
}

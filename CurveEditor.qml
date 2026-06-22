import QtQuick

// 톤 커브 에디터: 컨트롤 포인트를 드래그/추가/삭제하면 Catmull-Rom 보간 곡선이
// 256개 값(lut256)으로 계산되어 edited() 시그널로 전달된다(주인이 컨트롤러에 넘김).
//   - 빈 곳 클릭/드래그: 포인트 추가 후 이동
//   - 포인트 드래그: 이동 (양 끝점은 x 고정, y만)
//   - 포인트 더블클릭: 삭제 (양 끝점 제외)
Item {
    id: root
    // 채널별 톤커브: 0=RGB(마스터) 1=R 2=G 3=B. 채널마다 컨트롤포인트를 따로 보관하고
    // points 는 현재 channel 의 편집 대상(전환 시 swap, changed() 시 channelPoints 로 동기화).
    property int channel: 0
    property var channelPoints: [
        [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}],
        [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}],
        [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}],
        [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}]
    ]
    property var points: channelPoints[0]   // 정규화, x 오름차순
    onChannelChanged: { points = channelPoints[channel]; view.requestPaint() }
    readonly property real hitR: 0.045    // 포인트 적중 반경(정규화)
    signal edited()                        // 커브 변경 알림
    property var histogram: []             // 배경 히스토그램(256-bin, 0..1) — 주인이 바인딩
    onHistogramChanged: view.requestPaint()

    function clamp01(v) { return Math.max(0, Math.min(1, v)) }

    function evalAt(x) { return evalArr(points, x) }
    // 컨트롤 포인트를 통과하는 cubic Hermite (Catmull-Rom 탄젠트)
    function evalArr(p, x) {
        var n = p.length
        if (x <= p[0].x) return p[0].y
        if (x >= p[n - 1].x) return p[n - 1].y
        var k = 0
        while (k < n - 1 && x > p[k + 1].x) k++
        var p1 = p[k], p2 = p[k + 1]
        var p0 = p[Math.max(0, k - 1)], p3 = p[Math.min(n - 1, k + 2)]
        var h = p2.x - p1.x
        if (h <= 1e-6) return p1.y
        var s = (x - p1.x) / h
        var m1 = (p2.y - p0.y) / (p2.x - p0.x) * h
        var m2 = (p3.y - p1.y) / (p3.x - p1.x) * h
        var s2 = s * s, s3 = s2 * s
        return (2*s3 - 3*s2 + 1)*p1.y + (s3 - 2*s2 + s)*m1
             + (-2*s3 + 3*s2)*p2.y + (s3 - s2)*m2
    }

    // 특정 채널의 256개 커브 출력값(0..1)
    function lut256ch(ch) {
        var p = channelPoints[ch]
        var a = []
        for (var i = 0; i < 256; i++) a.push(clamp01(evalArr(p, i / 255)))
        return a
    }
    // 4채널 커브 [master, r, g, b] — 컨트롤러로 넘겨 합성 LUT 텍스처 생성에 사용
    function allLuts() { return [lut256ch(0), lut256ch(1), lut256ch(2), lut256ch(3)] }

    // 저장된 편집 복원용: 4채널 컨트롤포인트를 통째로 설정(주인이 setCurve 호출). edited() 미발화.
    function setChannelPoints(arr) {
        channelPoints = arr
        points = channelPoints[channel]
        view.requestPaint()
    }

    function nearest(nx, ny) {
        var best = -1, bd = hitR * hitR
        for (var i = 0; i < points.length; i++) {
            var dx = points[i].x - nx, dy = points[i].y - ny
            var d = dx*dx + dy*dy
            if (d < bd) { bd = d; best = i }
        }
        return best
    }
    function addPoint(nx, ny) {
        nx = clamp01(nx); ny = clamp01(ny)
        if (nx <= 0.0 || nx >= 1.0) return -1
        var arr = points.slice()
        var i = 0
        while (i < arr.length && arr[i].x < nx) i++
        arr.splice(i, 0, {x: nx, y: ny})
        points = arr; changed(); return i
    }
    function movePoint(i, nx, ny) {
        var arr = points.slice()
        ny = clamp01(ny)
        if (i === 0) nx = 0.0
        else if (i === arr.length - 1) nx = 1.0
        else nx = Math.max(arr[i-1].x + 0.001, Math.min(arr[i+1].x - 0.001, clamp01(nx)))
        arr[i] = {x: nx, y: ny}; points = arr; changed()
    }
    function removePoint(i) {
        if (i <= 0 || i >= points.length - 1) return
        var arr = points.slice(); arr.splice(i, 1); points = arr; changed()
    }
    // 현재 채널만 초기화
    function reset() { points = [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}]; changed() }
    // 4채널 모두 초기화(전역 Reset 용)
    function resetAll() {
        channelPoints = [
            [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}], [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}],
            [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}], [{x: 0.0, y: 0.0}, {x: 1.0, y: 1.0}]
        ]
        points = channelPoints[channel]; view.requestPaint(); edited()
    }
    // 편집된 현재 채널 포인트를 channelPoints 로 동기화 후 알림
    function changed() {
        var cp = channelPoints.slice(); cp[channel] = points; channelPoints = cp
        view.requestPaint(); edited()
    }

    // --- 보이는 에디터 ---
    Rectangle { anchors.fill: parent; color: "#1e1e1e"; border.color: "#444" }
    Canvas {
        id: view
        anchors.fill: parent
        onPaint: {
            var ctx = getContext('2d'), W = width, H = height
            ctx.clearRect(0, 0, W, H)
            // 배경 히스토그램 (sqrt 스케일로 낮은 빈도도 보이게)
            var hist = root.histogram
            if (hist && hist.length === 256) {
                ctx.fillStyle = "rgba(200,200,200,0.28)"
                ctx.beginPath(); ctx.moveTo(0, H)
                for (var hx = 0; hx < 256; hx++)
                    ctx.lineTo(hx/255*W, H - Math.sqrt(hist[hx]) * H * 0.92)
                ctx.lineTo(W, H); ctx.closePath(); ctx.fill()
            }
            // 그리드 (1/4 간격 = 4개 톤 구역 경계)
            ctx.strokeStyle = "#3a3a3a"; ctx.lineWidth = 1
            for (var g = 1; g < 4; g++) {
                ctx.beginPath(); ctx.moveTo(W*g/4, 0); ctx.lineTo(W*g/4, H); ctx.stroke()
                ctx.beginPath(); ctx.moveTo(0, H*g/4); ctx.lineTo(W, H*g/4); ctx.stroke()
            }
            // 라이트룸식 톤 구역 라벨
            ctx.fillStyle = "#8a8a8a"; ctx.font = "9px sans-serif"; ctx.textAlign = "center"
            var zlabels = ["Shadows", "Darks", "Lights", "Highlights"]
            for (var z = 0; z < 4; z++) ctx.fillText(zlabels[z], W*(z+0.5)/4, H - 4)
            var lineCol = ["#e8e8e8", "#ff6b6b", "#5fd16a", "#5b9cff"][root.channel]
            var ptCol = ["#ffcc33", "#ff8a8a", "#9be8a0", "#9bc0ff"][root.channel]
            ctx.strokeStyle = lineCol; ctx.lineWidth = 2; ctx.beginPath()
            for (var px = 0; px <= W; px += 2) {
                var py = (1 - root.evalAt(px / W)) * H
                if (px === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py)
            }
            ctx.stroke()
            for (var i = 0; i < root.points.length; i++) {
                var cx = root.points[i].x * W, cy = (1 - root.points[i].y) * H
                ctx.fillStyle = ptCol; ctx.beginPath(); ctx.arc(cx, cy, 5, 0, 6.2832); ctx.fill()
                ctx.strokeStyle = "#000"; ctx.lineWidth = 1; ctx.stroke()
            }
        }
    }
    MouseArea {
        anchors.fill: parent
        preventStealing: true      // ScrollView(Flickable)가 드래그를 가로채지 못하게
        property int dragIdx: -1
        onPressed: (mouse) => {
            var nx = mouse.x / width, ny = 1 - mouse.y / height
            var idx = root.nearest(nx, ny)
            if (idx < 0) idx = root.addPoint(nx, ny)
            dragIdx = idx
        }
        onPositionChanged: (mouse) => {
            if (dragIdx >= 0) root.movePoint(dragIdx, mouse.x / width, 1 - mouse.y / height)
        }
        onReleased: dragIdx = -1
        onDoubleClicked: (mouse) => {
            var idx = root.nearest(mouse.x / width, 1 - mouse.y / height)
            if (idx > 0 && idx < root.points.length - 1) root.removePoint(idx)
        }
    }
    Component.onCompleted: view.requestPaint()
}

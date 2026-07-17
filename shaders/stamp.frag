#version 440

// 날짜 스탬프 오버레이를 배경(파이프라인 결과)에 'screen'(가산)으로 합성한다.
// ⚠️예약 경로 — 현재 QML 은 stampOverlay(평범한 source-over Image)를 쓰고 이 셰이더는 미배선.
//   프리뷰를 export(screen+source-over 혼합)와 정합시키려는 향후 배선용으로 보존한다.
// LED 빛이 필름을 노광하듯 배경에 빛을 더함 — 어두운 곳엔 선명, 밝은 하이라이트에선
// 흰빛으로 씻겨 사라짐(export date_stamp.stamp_export 의 screen 합성과 동일 수식).
//   stampTex : 스탬프 스프라이트(image://stamp, premultiplied RGBA — Qt 기본)
//   bgTex    : 배경(canvasHolder ShaderEffectSource). bgMap=(u0,v0,du,dv) 로 uv 매핑.
//   strength : 스탬프 강도(STAMP_STRENGTH; export 의 sp.a*STAMP_STRENGTH 와 정합)

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    vec4  bgMap;      // (u0, v0, du, dv): bgUV = bgMap.xy + qt_TexCoord0 * bgMap.zw
    float strength;
    float screenMix;  // 1=순수 screen, 0=source-over. 밝은 배경 과다 소멸 완화용 혼합.
} ubuf;

layout(binding = 1) uniform sampler2D stampTex;
layout(binding = 2) uniform sampler2D bgTex;

void main() {
    vec2 uv = qt_TexCoord0;
    vec4 st = texture(stampTex, uv);                 // premultiplied: rgb = color*alpha
    vec2 bgUV = ubuf.bgMap.xy + uv * ubuf.bgMap.zw;
    vec3 bg = texture(bgTex, bgUV).rgb;
    float a = clamp(st.a * ubuf.strength, 0.0, 1.0);
    vec3 s = clamp(st.rgb * ubuf.strength, 0.0, 1.0);  // 방출광(premul rgb × 강도) = export 의 col*a
    vec3 over = bg * (1.0 - a) + s;                    // source-over
    vec3 scr = 1.0 - (1.0 - bg) * (1.0 - s);           // screen
    vec3 outc = mix(over, scr, ubuf.screenMix);        // 혼합(밝은 배경 과다 소멸 완화)
    fragColor = vec4(outc, 1.0) * ubuf.qt_Opacity;
}

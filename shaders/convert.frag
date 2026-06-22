#version 440

// 카메라 네이티브(감마 인코딩) src -> display sRGB 변환 사전패스.
// 블러 체인(텍스처/클래리티)과 메인 셰이더의 로컬대비 base(dispSrc)로 쓰인다.
// WB 는 as-shot 기준으로 고정(블러는 원래도 baked 기준이라 staleness 동일).
// = [선형화 -> as-shot 상대게인(카메라공간) -> cam->sRGB 매트릭스 -> OETF].

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float relR; float relG; float relB;     // as-shot WB 상대게인 (TREF 대비)
    float camM0; float camM1; float camM2;   // cam 네이티브 -> 선형 sRGB (행우선)
    float camM3; float camM4; float camM5;
    float camM6; float camM7; float camM8;
} ubuf;

layout(binding = 1) uniform sampler2D src;

vec3 srgbToLinear(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}
vec3 linearToSrgb(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}
vec3 applyCamMat(vec3 v) {
    return vec3(dot(vec3(ubuf.camM0, ubuf.camM1, ubuf.camM2), v),
                dot(vec3(ubuf.camM3, ubuf.camM4, ubuf.camM5), v),
                dot(vec3(ubuf.camM6, ubuf.camM7, ubuf.camM8), v));
}

// adjust.frag 와 동일: 헤드룸 디코드(×H) + 단일 필름릭 베이스 톤커브.
const float PROXY_HEADROOM = 4.0;
const float HL_KNEE = 0.7;
vec3 filmic(vec3 x) {
    vec3 hi = max(x - HL_KNEE, 0.0);
    vec3 rolled = 1.0 - (1.0 - HL_KNEE) * exp(-hi / (1.0 - HL_KNEE));
    vec3 shoulder = mix(x, rolled, step(vec3(HL_KNEE), x));
    return linearToSrgb(shoulder);
}

void main() {
    // dispSrc = 블러/로컬대비 base. 헤드룸 디코드 → as-shot WB → 매트릭스 → filmic(노출 무관 중성).
    vec3 cam = srgbToLinear(texture(src, qt_TexCoord0).rgb) * PROXY_HEADROOM;
    cam *= vec3(ubuf.relR, ubuf.relG, ubuf.relB);
    fragColor = vec4(filmic(applyCamMat(cam)), 1.0) * ubuf.qt_Opacity;
}

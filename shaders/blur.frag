#version 440

// 분리형(1D) 가우시안 블러 9-tap. dir 로 수평/수직 방향과 탭 간격을 지정.
// 텍스처/클래리티 로컬 대비용 블러를 만들 때 H, V 두 번 적용한다.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    vec2  dir;        // 탭 간 텍스처좌표 오프셋 (수평=(s,0), 수직=(0,s))
} ubuf;

layout(binding = 1) uniform sampler2D src;

void main() {
    float w0 = 0.227027, w1 = 0.1945946, w2 = 0.1216216, w3 = 0.054054, w4 = 0.016216;
    vec2 uv = qt_TexCoord0;
    vec3 c = texture(src, uv).rgb * w0;
    c += texture(src, uv + ubuf.dir * 1.0).rgb * w1;
    c += texture(src, uv - ubuf.dir * 1.0).rgb * w1;
    c += texture(src, uv + ubuf.dir * 2.0).rgb * w2;
    c += texture(src, uv - ubuf.dir * 2.0).rgb * w2;
    c += texture(src, uv + ubuf.dir * 3.0).rgb * w3;
    c += texture(src, uv - ubuf.dir * 3.0).rgb * w3;
    c += texture(src, uv + ubuf.dir * 4.0).rgb * w4;
    c += texture(src, uv - ubuf.dir * 4.0).rgb * w4;
    fragColor = vec4(c, 1.0) * ubuf.qt_Opacity;
}

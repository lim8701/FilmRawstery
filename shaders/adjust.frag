#version 440

// Qt6 ShaderEffect 용 프래그먼트 셰이더 (Vulkan 스타일 GLSL).
// pyside6-qsb 로 .qsb 로 컴파일한 뒤 ShaderEffect.fragmentShader 에서 참조한다.

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4  qt_Matrix;
    float qt_Opacity;
    float exposure;     // 노출 (stop)
    float contrast;     // 대비 (1.0=무변화)
    float wbR;          // WB 프리뷰 게인 (커밋되면 1)
    float wbG;
    float wbB;
    float highlights;   // 톤 영역 (-1..1)
    float shadows;
    float whites;
    float blacks;
    float texAmt;       // 텍스처 (-1..1) 중주파 로컬대비
    float clarity;      // 클래리티 (-1..1) 중간톤 로컬대비(큰 반경)
    float dehaze;       // 디헤이즈 (-1..1) +대비/채도/로컬대비 / -흰베일·플랫
    float vignette;     // 비네팅 (-1..1) 음수=가장자리 어둡게
    float lutSize;      // 3D LUT 한 변 N
    float lutStrength;  // 필름시뮬 강도 0..1 (1=LUT 그대로, 0=미적용)
    int   lutEnabled;   // 0=미적용
} ubuf;

layout(binding = 1) uniform sampler2D src;       // 원본 이미지
layout(binding = 2) uniform sampler2D lut;       // 3D LUT 아틀라스 (N*N x N)
layout(binding = 3) uniform sampler2D curve;     // 톤 커브 1D LUT (256x1)
layout(binding = 4) uniform sampler2D texBlur;   // src 가우시안 블러(작은 반경)
layout(binding = 5) uniform sampler2D claBlur;   // src 가우시안 블러(큰 반경)

const vec3 LUMA = vec3(0.299, 0.587, 0.114);

// 톤 영역별 (휘도 보존 luma 오프셋)
vec3 tone_zones(vec3 c, float hi, float sh, float wh, float bl) {
    float l = dot(c, LUMA);
    float shMask = 1.0 - smoothstep(0.0, 0.5, l);
    float hiMask = smoothstep(0.5, 1.0, l);
    float blMask = 1.0 - smoothstep(0.0, 0.25, l);
    float whMask = smoothstep(0.75, 1.0, l);
    float delta = sh * 0.3 * shMask + hi * 0.3 * hiMask
                + bl * 0.3 * blMask + wh * 0.3 * whMask;
    return c + vec3(delta);
}

vec3 lut_texel(float ri, float gi, float bi, float N) {
    float x = (bi * N + ri + 0.5) / (N * N);
    float y = (gi + 0.5) / N;
    return texture(lut, vec2(x, y)).rgb;
}

vec3 apply_lut(vec3 col, float N) {
    vec3 c = clamp(col, 0.0, 1.0) * (N - 1.0);
    vec3 b0 = floor(c);
    vec3 b1 = min(b0 + 1.0, N - 1.0);
    vec3 f  = c - b0;
    vec3 c000 = lut_texel(b0.r, b0.g, b0.b, N);
    vec3 c100 = lut_texel(b1.r, b0.g, b0.b, N);
    vec3 c010 = lut_texel(b0.r, b1.g, b0.b, N);
    vec3 c110 = lut_texel(b1.r, b1.g, b0.b, N);
    vec3 c001 = lut_texel(b0.r, b0.g, b1.b, N);
    vec3 c101 = lut_texel(b1.r, b0.g, b1.b, N);
    vec3 c011 = lut_texel(b0.r, b1.g, b1.b, N);
    vec3 c111 = lut_texel(b1.r, b1.g, b1.b, N);
    vec3 c00 = mix(c000, c100, f.r);
    vec3 c01 = mix(c001, c101, f.r);
    vec3 c10 = mix(c010, c110, f.r);
    vec3 c11 = mix(c011, c111, f.r);
    vec3 c0  = mix(c00, c10, f.g);
    vec3 c1  = mix(c01, c11, f.g);
    return mix(c0, c1, f.b);
}

void main() {
    vec2 uv = qt_TexCoord0;
    vec3 rgb = texture(src, uv).rgb;

    // 1) 노출
    rgb *= pow(2.0, ubuf.exposure);
    // 2) WB 프리뷰 게인
    rgb *= vec3(ubuf.wbR, ubuf.wbG, ubuf.wbB);
    rgb = clamp(rgb, 0.0, 1.0);
    // 3) 톤 영역별
    rgb = clamp(tone_zones(rgb, ubuf.highlights, ubuf.shadows, ubuf.whites, ubuf.blacks), 0.0, 1.0);

    vec3 s0 = texture(src, uv).rgb;              // 원본(블러 비교용)

    // 4) 텍스처 — 중주파 디테일 (원본 - 작은반경 블러)
    if (ubuf.texAmt != 0.0) {
        rgb += (s0 - texture(texBlur, uv).rgb) * ubuf.texAmt * 1.6;
    }

    // 5) 클래리티 — 중간톤 로컬대비 (휘도, 큰 반경 블러)
    if (ubuf.clarity != 0.0) {
        float d = dot(s0, LUMA) - dot(texture(claBlur, uv).rgb, LUMA);
        float l = dot(rgb, LUMA);
        float mid = 1.0 - abs(2.0 * l - 1.0);    // 중간톤 가중
        rgb += d * ubuf.clarity * 0.8 * mid;
    }

    // 6) 디헤이즈 — 톤 모델 (라이트룸 느낌)
    //    +: 로컬대비 + 대비 + 채도(안개 걷힘)  /  -: 흰 베일로 밝게 + 대비/채도 ↓(안개)
    if (ubuf.dehaze != 0.0) {
        float ld = dot(s0, LUMA) - dot(texture(claBlur, uv).rgb, LUMA);
        rgb += ld * ubuf.dehaze * 0.4;                         // 로컬 대비
        rgb = (rgb - 0.5) * (1.0 + ubuf.dehaze * 0.25) + 0.5;  // 대비
        if (ubuf.dehaze < 0.0) {
            rgb = mix(rgb, vec3(0.92), (-ubuf.dehaze) * 0.22); // 흰 베일(밝아짐)
        }
        float l = dot(rgb, LUMA);
        rgb = mix(vec3(l), rgb, 1.0 + ubuf.dehaze * 0.3);      // 채도
    }
    rgb = clamp(rgb, 0.0, 1.0);

    // 7) 필름 시뮬레이션 3D LUT (강도 블렌딩 = 라이트룸 프로파일 Amount)
    if (ubuf.lutEnabled != 0) {
        vec3 looked = apply_lut(rgb, ubuf.lutSize);
        rgb = mix(rgb, looked, ubuf.lutStrength);
    }

    // 8) 대비
    rgb = clamp((rgb - 0.5) * ubuf.contrast + 0.5, 0.0, 1.0);

    // 9) 톤 커브 (RGB 공통)
    rgb.r = texture(curve, vec2(rgb.r, 0.5)).r;
    rgb.g = texture(curve, vec2(rgb.g, 0.5)).r;
    rgb.b = texture(curve, vec2(rgb.b, 0.5)).r;

    // 10) 비네팅 (방사형, 맨 끝)
    if (ubuf.vignette != 0.0) {
        float r = length(uv - 0.5) / 0.7071;
        rgb *= 1.0 + ubuf.vignette * 0.8 * smoothstep(0.35, 1.0, r);
    }

    rgb = clamp(rgb, 0.0, 1.0);
    fragColor = vec4(rgb, 1.0) * ubuf.qt_Opacity;
}

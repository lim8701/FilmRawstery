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
    float grainAmt;     // 필름 그레인 강도 0..1 (0=미적용)
    float grainSize;    // 입자 크기 0..1 (0=미세, 1=굵음)
    float grainAspect;  // 프록시 가로/세로비 W/H (정사각 입자용)
    float stampOn;      // 날짜 스탬프 표시 1/0
    float stampStrength;// 날짜 스탬프 가산 강도
    float saturation;   // 채도 (-1..1, 0=무변화, -1=흑백)
    float vibrance;     // 바이브런스 (-1..1, 저채도 우선 보정)
    float sharpenAmt;   // 샤프닝 강도 0..1 (USM, 휘도)
    float sharpenDetail;// 샤프닝 디테일 0..1 (미세 고주파 추가 강조)
    float sharpenMask;  // 샤프닝 마스킹 0..1 (1=강한 엣지 한정)
    float texelW;       // 1/procW (마스킹 그래디언트 스텝)
    float texelH;       // 1/procH
    // 카메라 네이티브 RGB -> 선형 sRGB 매트릭스 (행우선 9개, wb.cam_to_srgb_matrix)
    float camM0; float camM1; float camM2;
    float camM3; float camM4; float camM5;
    float camM6; float camM7; float camM8;
} ubuf;

layout(binding = 1) uniform sampler2D src;       // 원본(카메라네이티브 감마 인코딩)
layout(binding = 2) uniform sampler2D lut;       // 3D LUT 아틀라스 (N*N x N)
layout(binding = 3) uniform sampler2D curve;     // 톤 커브 1D LUT (256x1)
layout(binding = 4) uniform sampler2D texBlur;   // dispSrc 가우시안 블러(작은 반경)
layout(binding = 5) uniform sampler2D claBlur;   // dispSrc 가우시안 블러(큰 반경)
layout(binding = 6) uniform sampler2D stampTex;  // 날짜 스탬프 오버레이(프록시 RGBA)
layout(binding = 7) uniform sampler2D dispSrc;   // src 의 display sRGB 변환본(블러/로컬대비 base)
layout(binding = 8) uniform sampler2D sharpBlur; // dispSrc 가우시안 블러(샤프닝 반경, 가변)

const vec3 LUMA = vec3(0.299, 0.587, 0.114);

// sRGB <-> linear (정확 EOTF, rawpy gamma=(2.4,12.92) 와 정합)
vec3 srgbToLinear(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c / 12.92, pow((c + 0.055) / 1.055, vec3(2.4)), step(0.04045, c));
}
vec3 linearToSrgb(vec3 c) {
    c = clamp(c, 0.0, 1.0);
    return mix(c * 12.92, 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055, step(0.0031308, c));
}
// 행우선 9-float 매트릭스 적용 (GLSL mat3 열우선 혼동 회피)
vec3 applyCamMat(vec3 v) {
    return vec3(dot(vec3(ubuf.camM0, ubuf.camM1, ubuf.camM2), v),
                dot(vec3(ubuf.camM3, ubuf.camM4, ubuf.camM5), v),
                dot(vec3(ubuf.camM6, ubuf.camM7, ubuf.camM8), v));
}

// 의사난수 해시 + value noise (필름 그레인용, 절차적·결정적)
// hash12: Dave Hoskins (https://www.shadertoy.com/view/4djSRW) — 곱셈해시의
// 세로/대각 줄무늬 아티팩트를 피한 고품질 해시.
float hash12(vec2 p) {
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
float valueNoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash12(i), b = hash12(i + vec2(1.0, 0.0));
    float c = hash12(i + vec2(0.0, 1.0)), d = hash12(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}

// 톤 영역별
//  - 하이라이트/섀도우 = 국소 노출(멀티플리커티브 게인 c*2^g): 색비·대비 보존, 회색화 방지.
//    ★마스크는 '국소 평균 휘도'(lb=큰반경 블러 휘도)로 계산 → 라이트룸식 로컬 톤맵.
//    픽셀 휘도로 마스킹하면 어두운 영역 속 밝은 디테일이 안 올라가 밋밋·이질적이라
//    라이트룸과 느낌이 다름. 주변 밝기로 판단해 영역째 들어올리고 로컬 대비는 보존한다.
//  - 화이트/블랙 = 끝단 레벨(가산, 픽셀 휘도): 화이트/블랙 포인트(클리핑 지점) 이동.
vec3 tone_zones(vec3 c, float lb, float hi, float sh, float wh, float bl) {
    // 라이트룸식: 범위를 넓혀 미드톤(0.25~0.75)에서 shadows/highlights 가 겹치게.
    float shMask = 1.0 - smoothstep(0.0, 0.75, lb);
    float hiMask = smoothstep(0.25, 1.0, lb);
    c *= exp2(sh * 1.0 * shMask + hi * 1.0 * hiMask);     // 국소 노출(stop)
    float l = dot(c, LUMA);
    float whMask = smoothstep(0.75, 1.0, l);              // 화이트/블랙은 끝단(좁게) 유지
    float blMask = 1.0 - smoothstep(0.0, 0.25, l);
    c += vec3(wh * 0.3 * whMask + bl * 0.3 * blMask);     // 끝단 레벨 이동
    return c;
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

    // 0) WB (카메라 네이티브 공간, linear) -> cam->sRGB 매트릭스 -> display sRGB
    //    src 는 감마 인코딩된 카메라 네이티브. 선형화 후 WB 상대게인(TREF 대비),
    //    매트릭스로 sRGB(linear) 변환, OETF 로 display sRGB. = rawpy 내부 수학 재현.
    vec3 cam = srgbToLinear(texture(src, uv).rgb);
    cam *= vec3(ubuf.wbR, ubuf.wbG, ubuf.wbB);
    vec3 rgb = linearToSrgb(applyCamMat(cam));

    // 0.5) 하이라이트 디새추레이션(클리핑 롤오프): 한 채널만 포화돼 생기는 색끼
    //      (예: 불꽃 코어 청록)를 제거 — 최댓값이 포화에 가까울수록 중성(흰색)으로.
    //      카메라/라이트룸 하이라이트 거동. 휘도(최댓값) 보존하며 색만 뺀다.
    {
        float mx = max(rgb.r, max(rgb.g, rgb.b));
        rgb = mix(rgb, vec3(mx), smoothstep(0.95, 1.0, mx));
    }

    // 1) 노출 (display 공간). ★1.0 을 넘는 하이라이트를 여기서 클램프하지 않는다 —
    //    헤드룸을 유지해야 뒤의 highlights- 로 다시 끌어내려 디테일을 복원할 수 있다
    //    (클램프하면 모두 1.0 으로 뭉개져 'white hole' 이 평평한 흰판으로 남는다).
    //    상한은 디헤이즈 뒤 clamp(LUT 직전)에서 [0,1] 로 잡는다.
    rgb *= pow(2.0, ubuf.exposure);
    rgb = max(rgb, 0.0);
    // 3) 톤 영역별 — hi/sh 마스크는 국소 평균 휘도(claBlur)로 계산(라이트룸식 로컬 톤맵).
    //    claBlur 는 dispSrc(노출 전) 블러라 노출을 반영하려 같은 게인을 곱한다.
    float lb = dot(texture(claBlur, uv).rgb, LUMA) * pow(2.0, ubuf.exposure);
    rgb = max(tone_zones(rgb, lb, ubuf.highlights, ubuf.shadows, ubuf.whites, ubuf.blacks), 0.0);

    vec3 s0 = texture(dispSrc, uv).rgb;          // display sRGB 변환본(블러 비교용)

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

    // 5.5) 샤프닝 — 언샤프 마스크(휘도). 반경 블러 고주파 + Detail 미세 고주파,
    //      Masking 으로 엣지 한정(평탄부 노이즈 증폭 억제). 휘도에만 가산 → 색 불변.
    if (ubuf.sharpenAmt > 0.0) {
        float Ld = dot(s0, LUMA);                            // dispSrc 휘도
        float Lr = dot(texture(sharpBlur, uv).rgb, LUMA);    // 반경 블러 휘도
        float Lt = dot(texture(texBlur, uv).rgb, LUMA);      // 미세 블러 휘도
        float hp = (Ld - Lr) + ubuf.sharpenDetail * (Ld - Lt);
        float gx = dot(texture(dispSrc, uv + vec2(ubuf.texelW, 0.0)).rgb, LUMA)
                 - dot(texture(dispSrc, uv - vec2(ubuf.texelW, 0.0)).rgb, LUMA);
        float gy = dot(texture(dispSrc, uv + vec2(0.0, ubuf.texelH)).rgb, LUMA)
                 - dot(texture(dispSrc, uv - vec2(0.0, ubuf.texelH)).rgb, LUMA);
        float edge = smoothstep(0.0, 0.06, length(vec2(gx, gy)));
        float mask = mix(1.0, edge, ubuf.sharpenMask);
        rgb += vec3(hp * ubuf.sharpenAmt * 1.5 * mask);
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

    // 7.5) 바이브런스/채도 (luma 축 mix -> 휘도 보존)
    if (ubuf.vibrance != 0.0) {
        float l = dot(rgb, LUMA);
        float cur = max(rgb.r, max(rgb.g, rgb.b)) - min(rgb.r, min(rgb.g, rgb.b));
        float f = 1.0 + ubuf.vibrance * (1.0 - clamp(cur, 0.0, 1.0));  // 저채도일수록 강하게
        rgb = clamp(mix(vec3(l), rgb, f), 0.0, 1.0);
    }
    if (ubuf.saturation != 0.0) {
        float l = dot(rgb, LUMA);
        rgb = clamp(mix(vec3(l), rgb, 1.0 + ubuf.saturation), 0.0, 1.0);
    }

    // 8) 대비
    rgb = clamp((rgb - 0.5) * ubuf.contrast + 0.5, 0.0, 1.0);

    // 9) 톤 커브 (RGB 공통)
    rgb.r = texture(curve, vec2(rgb.r, 0.5)).r;
    rgb.g = texture(curve, vec2(rgb.g, 0.5)).r;
    rgb.b = texture(curve, vec2(rgb.b, 0.5)).r;

    // 10) 비네팅 (방사형)
    if (ubuf.vignette != 0.0) {
        float r = length(uv - 0.5) / 0.7071;
        rgb *= 1.0 + ubuf.vignette * 0.8 * smoothstep(0.35, 1.0, r);
    }

    // 11) 날짜 스탬프 (필름 데이트백) — 하이브리드 합성:
    //     코어(또렷한 숫자)=source-over로 배경무관 일관, 헤일로=screen 가산으로 빛 번짐.
    //     비네팅 뒤(LED는 렌즈를 거치지 않아 비네팅 영향 없음).
    if (ubuf.stampOn > 0.5) {
        vec4 st = texture(stampTex, uv);
        float a = clamp(st.a * ubuf.stampStrength, 0.0, 1.0);
        float coreA = smoothstep(0.45, 0.85, a) * 0.70;   // 코어 불투명도 상한(배경 비침)
        rgb = mix(rgb, st.rgb, coreA);                    // 코어 source-over (일관)
        vec3 glow = st.rgb * clamp(a * (1.0 - coreA * 0.5) * 1.2, 0.0, 1.0);  // 빛 가산(게인1.2)
        rgb = 1.0 - (1.0 - rgb) * (1.0 - glow);           // screen 가산 (코어도 일부 태움)
    }

    // 12) 필름 그레인 (에멀전 입자) — 맨 끝: 장면과 날짜 스탬프 모두에 입혀짐.
    if (ubuf.grainAmt > 0.0) {
        float gridN = mix(1500.0, 500.0, ubuf.grainSize);
        vec2 gco = uv * vec2(gridN, gridN / ubuf.grainAspect);
        float n = valueNoise(gco) - 0.5;
        rgb += n * ubuf.grainAmt * 0.12;
    }

    rgb = clamp(rgb, 0.0, 1.0);
    fragColor = vec4(rgb, 1.0) * ubuf.qt_Opacity;
}

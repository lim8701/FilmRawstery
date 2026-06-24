"""하늘 자동 세그멘테이션 엔진 (ONNX / SegFormer-B0 @ ADE20K).

라이트룸 "Select Sky" 식 하늘 마스크를 ML 시맨틱 세그멘테이션으로 생성한다.
PySide6/QML 비의존 — numpy in/out 의 독립 모듈이라 export 파이프라인에서도 재사용 가능.

파이프라인:
  display-sRGB RGB(uint8) → 종횡비 유지 고해상도 리사이즈 → ImageNet 정규화 → SegFormer ONNX 추론
  → 150클래스 logits → softmax → sky(=2) 확률맵(입력의 1/4)
  → 입력 해상도로 업샘플 → (옵션) 원본 휘도 기준 guided filter 로 엣지 정제 → soft alpha[0,1]

  ⚠️입력을 정사각 512 로 '찌부리면' 종횡비 왜곡 + 저해상도라 가는 가지/전선/경계가 뭉갠다.
    종횡비 유지 + 긴 변 INPUT_LONG_EDGE(32배수)로 넣어야 경계 디테일이 산다(실측 확인).

모델: Xenova/segformer-b0-finetuned-ade-512-512 의 사전 export ONNX(fp32, ~14MB).
최초 호출 시 자동 다운로드(models/ 캐시). torch/transformers 불필요(onnxruntime 만 사용).
"""

import os
import urllib.request

import numpy as np
from scipy.ndimage import binary_fill_holes, uniform_filter, zoom

# ── 모델 ────────────────────────────────────────────────────────────────────
# SegFormer-B2(ADE20K). B0(~3.7M)는 채광창+구름 등 까다로운 장면에서 하늘을 windowpane 으로
# 오분류·확신도 약함 → B2(~27M)로 상향(하늘 인식·구름 채움 큰 개선, 실측 확인). 모든 변형이
# sky=클래스2·ImageNet 정규화 동일. 더 키우려면 b4/b5(640) 가능하나 크기·속도 급증(b5 324MB·~4s).
_REPO = "Xenova/segformer-b2-finetuned-ade-512-512"
_MODEL_URL = f"https://huggingface.co/{_REPO}/resolve/main/onnx/model.onnx"
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "segformer_b2_ade.onnx")

# ── SegFormer 전처리 (preprocessor_config.json 와 일치) ──────────────────────
# 추론 입력 긴 변(종횡비 유지, 각 변 32의 배수=SegFormer stride 로 라운딩).
# ↑ 키우면 가는 가지/전선/경계 디테일↑, 추론 시간↓(1024≈280ms, 1536≈900ms @ proxy). 튜닝 대상.
INPUT_LONG_EDGE = 1024
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_SKY_CLASS = 2                                 # ADE20K id2label["2"] == "sky"
_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# ── 정제 계수 (라이트룸 비교로 튜닝 대상) ────────────────────────────────────
# 마스크 결정 곡선: softmax 확률을 smoothstep(LO,HI)로 정형. 모델은 약확신 하늘(채광창 유리 너머
# 밝은 구름은 windowpane 으로 혼동해 sky prob 0.05~0.3)을 낮게 줘서, 임계값이 높으면 구름이 구멍이
# 된다. LO/HI 를 낮춰 약확신 하늘까지 solid 로 포함(진짜 비하늘=램프·건물·지면은 prob≈0 이라 제외
# 유지). 부수효과: 구름이 solid(1.0)가 되어 guided filter 의 '밝은 영역 억제'도 사라진다.
# ⚠️더 낮추면 오검출(밝은 벽 등)↑. 검증: DSCF8012(구름 채움)·DSCF7127(가지 보존·건물 제외).
MASK_LO = 0.02
MASK_HI = 0.20
# 구멍 채우기: 하늘로 완전히 둘러싸인 영역(=밝은 구름 등 모델이 약하게 본 곳)을 solid 하게 채움.
# binary_fill_holes 는 '에워싸인' 구멍만 채우므로(나무 줄기처럼 바깥/지면과 이어진 건 안 채움 → 안전)
# 하늘 속 구름 구멍 제거에 적합. FILL_T = 채우기 판단 임계.
MASK_FILL_T = 0.5
GUIDED_RADIUS_FRAC = 0.012   # guided filter 반경 = 짧은 변 × 이 비율 (해상도 독립)
GUIDED_EPS = 1e-4            # guided filter 정규화(엣지 보존 강도; 작을수록 엣지 밀착)

_session_obj = None


def ensure_model() -> str:
    """모델 파일 보장(없으면 다운로드). 경로 반환."""
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        tmp = MODEL_PATH + ".part"
        urllib.request.urlretrieve(_MODEL_URL, tmp)   # ~14MB
        os.replace(tmp, MODEL_PATH)                    # 원자적 교체(부분파일 방지)
    return MODEL_PATH


def _session():
    """캐시된 ONNX Runtime 세션(CPU)."""
    global _session_obj
    if _session_obj is None:
        import onnxruntime as ort
        ensure_model()
        _session_obj = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])
    return _session_obj


def _resize(arr: np.ndarray, out_hw, order=1) -> np.ndarray:
    """(H,W) 또는 (H,W,C) bilinear 리사이즈."""
    h, w = arr.shape[:2]
    oh, ow = out_hw
    if (h, w) == (oh, ow):
        return arr
    factors = [oh / h, ow / w] + ([1.0] if arr.ndim == 3 else [])
    return zoom(arr, factors, order=order)


def _infer_size(h: int, w: int):
    """종횡비 유지 + 긴 변=INPUT_LONG_EDGE, 각 변을 32의 배수(SegFormer stride)로 라운딩."""
    le = max(h, w)
    s = INPUT_LONG_EDGE / float(le) if le > 0 else 1.0
    ih = max(32, int(round(h * s / 32.0)) * 32)
    iw = max(32, int(round(w * s / 32.0)) * 32)
    return ih, iw


def _preprocess(rgb_u8: np.ndarray) -> np.ndarray:
    """RGB(H,W,3 uint8) → NCHW float32. 종횡비 유지 고해상도(찌부림 방지) + /255 + ImageNet 정규화."""
    ih, iw = _infer_size(*rgb_u8.shape[:2])
    x = _resize(rgb_u8.astype(np.float32), (ih, iw), order=1) / 255.0
    x = (x - _MEAN) / _STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)  # (1,3,ih,iw)


def _sky_prob(rgb_u8: np.ndarray) -> np.ndarray:
    """추론 → sky 클래스 softmax 확률맵 (Hm,Wm) float32. (보통 128×128)"""
    sess = _session()
    inp = sess.get_inputs()[0].name
    out = sess.get_outputs()[0].name
    logits = sess.run([out], {inp: _preprocess(rgb_u8)})[0][0]   # (150, Hm, Wm)
    logits = logits - logits.max(axis=0, keepdims=True)          # softmax 수치안정
    e = np.exp(logits)
    prob = e[_SKY_CLASS] / e.sum(axis=0)
    return prob.astype(np.float32)


def _smoothstep(e0: float, e1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def _guided_filter(guide: np.ndarray, src: np.ndarray, radius: int, eps: float) -> np.ndarray:
    """He et al. guided filter. guide/src 는 [0,1] (H,W). 가이드 엣지에 src 를 밀착시킨다."""
    radius = max(1, int(radius))
    size = 2 * radius + 1
    mean_g = uniform_filter(guide, size)
    mean_s = uniform_filter(src, size)
    mean_gs = uniform_filter(guide * src, size)
    cov_gs = mean_gs - mean_g * mean_s
    mean_gg = uniform_filter(guide * guide, size)
    var_g = mean_gg - mean_g * mean_g
    a = cov_gs / (var_g + eps)
    b = mean_s - a * mean_g
    return uniform_filter(a, size) * guide + uniform_filter(b, size)


def segment_sky(rgb_u8: np.ndarray, refine: bool = True, fill_holes: bool = True) -> np.ndarray:
    """하늘 마스크(soft alpha, float32 [0,1], 입력과 동일 H×W) 반환.

    rgb_u8: display-sRGB RGB (H,W,3) uint8.
    fill_holes=True 면 하늘로 에워싸인 구멍(밝은 구름 등)을 채움.
    refine=True 면 원본 휘도를 가이드로 guided filter 정제(나뭇가지/건물 경계 밀착).
    하드 마스크가 필요하면 호출측에서 threshold(예: mask > 0.5).
    """
    h, w = rgb_u8.shape[:2]
    prob = _sky_prob(rgb_u8)
    mask = _resize(prob, (h, w), order=1).astype(np.float32)     # 입력 해상도 업샘플
    mask = _smoothstep(MASK_LO, MASK_HI, mask)                   # 결정 곡선: 내부 solid·경계 soft
    if fill_holes:
        # 하늘에 에워싸인 구멍(구름)을 solid 로. 에워싸이지 않은 것(줄기·프레임)은 그대로 → 안전.
        filled = binary_fill_holes(mask > MASK_FILL_T)
        mask = np.maximum(mask, filled.astype(np.float32))
    if refine:
        luma = (rgb_u8.astype(np.float32) / 255.0) @ _LUMA
        r = max(1, int(min(h, w) * GUIDED_RADIUS_FRAC))
        mask = _guided_filter(luma, mask, r, GUIDED_EPS)
    return np.clip(mask, 0.0, 1.0)

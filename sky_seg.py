"""시맨틱 세그멘테이션 마스킹 엔진 (ONNX / SegFormer-B2 @ ADE20K, 150클래스).

라이트룸식 영역 마스크를 ML 세그로 생성한다. 추론 1회로 150클래스 softmax 를 모두 얻으므로,
원하는 클래스(또는 여러 클래스의 합집합)를 골라 **복합 마스크**를 만들 수 있다(Sky/Vegetation/
Building/Water/…). PySide6/QML 비의존 — numpy in/out 독립 모듈(export 파이프라인에서도 재사용).

2단계 사용(라이브 갱신용):
  infer_softmax(rgb)            → (probs[150,hm,wm], (H,W))   # 추론 1회, 캐시
  compose_mask(probs, ..., ids) → soft alpha[0,1]            # 선택 클래스 합산 → 후처리(빠름)

후처리: 선택 클래스 확률 합산 → 입력 해상도 업샘플 → 결정 곡선(smoothstep) → 구멍 채우기
       → 원본 휘도 기준 guided filter 엣지 정제.
  ⚠️입력을 정사각 512 로 '찌부리면' 종횡비 왜곡 + 저해상도라 가는 가지/전선/경계가 뭉갠다.
    종횡비 유지 + 긴 변 INPUT_LONG_EDGE(32배수)로 넣어야 경계 디테일이 산다(실측 확인).

모델: Xenova/segformer-b2-finetuned-ade-512-512 의 사전 export ONNX(fp32, ~105MB).
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

# ── 마스크 클래스 그룹 (UI 체크박스) ─────────────────────────────────────────
# (key, 표시이름, ADE20K 인덱스 목록). 체크된 그룹들의 인덱스를 합집합으로 모아 복합 마스크 생성.
# 인덱스는 사진 편집 관점 묶음(예: 초목=나무+풀+식물). 자유롭게 추가/수정 가능.
MASK_GROUPS = [
    ("sky",        "Sky",        [2]),
    ("vegetation", "Vegetation", [4, 9, 17, 66]),     # tree, grass, plant, flower
    ("building",   "Building",   [1, 25, 48]),        # building, house, skyscraper
    ("ground",     "Ground",     [6, 11, 13, 29, 46]),# road, sidewalk, earth, field, sand
    ("water",      "Water",      [21, 26, 60, 109, 128]),  # water, sea, river, pool, lake
    ("mountain",   "Mountain",   [16, 34]),           # mountain, rock
    ("person",     "Person",     [12]),
]
_GROUP_IDS = {k: ids for k, _, ids in MASK_GROUPS}


def class_ids_for(keys):
    """그룹 key 목록 → ADE 인덱스 합집합(정렬)."""
    out = set()
    for k in keys:
        out.update(_GROUP_IDS.get(str(k), []))
    return sorted(out)


def groups_for_qml():
    """QML 체크박스용 [{key,label}, ...]."""
    return [{"key": k, "label": lbl} for k, lbl, _ in MASK_GROUPS]

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


def infer_softmax(rgb_u8: np.ndarray):
    """추론 1회 → 전체 150클래스 softmax 확률맵. (probs[150,Hm,Wm] float32, 원본 (H,W)) 반환.
    Hm,Wm ≈ 입력/4. 복합 마스크용으로 캐시해 두고 compose_mask 로 여러 번 재조합한다."""
    sess = _session()
    inp = sess.get_inputs()[0].name
    out = sess.get_outputs()[0].name
    logits = sess.run([out], {inp: _preprocess(rgb_u8)})[0][0]   # (150, Hm, Wm)
    logits = logits - logits.max(axis=0, keepdims=True)          # softmax 수치안정
    e = np.exp(logits)
    probs = (e / e.sum(axis=0)).astype(np.float32)
    return probs, tuple(rgb_u8.shape[:2])


def compose_mask(probs, out_hw, class_ids, guide_luma=None, refine=True, fill_holes=True):
    """선택 클래스 합산 → 마스크(soft alpha float32 [0,1], out_hw). 추론 없이 빠르게 재조합.

    probs: infer_softmax 결과(150,Hm,Wm). class_ids: 합집합할 ADE 인덱스(빈 목록=빈 마스크).
    guide_luma: 원본 휘도(H,W)[0,1] — guided filter 가이드(refine=True 시 필요).
    softmax 확률이라 선택 채널 합 = P(픽셀이 선택 클래스 중 하나)."""
    h, w = out_hw
    if not class_ids:
        return np.zeros((h, w), dtype=np.float32)
    p = probs[list(class_ids)].sum(axis=0)                       # 합집합 확률(저해상도)
    mask = _resize(p, (h, w), order=1).astype(np.float32)        # 입력 해상도 업샘플
    mask = _smoothstep(MASK_LO, MASK_HI, mask)                   # 결정 곡선
    if fill_holes:
        mask = np.maximum(mask, binary_fill_holes(mask > MASK_FILL_T).astype(np.float32))
    if refine and guide_luma is not None:
        r = max(1, int(min(h, w) * GUIDED_RADIUS_FRAC))
        mask = _guided_filter(guide_luma, mask, r, GUIDED_EPS)
    return np.clip(mask, 0.0, 1.0)


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
    """편의 함수: 단일 추론 → 하늘(sky) 마스크. (복합 마스크는 infer_softmax + compose_mask 사용)"""
    probs, hw = infer_softmax(rgb_u8)
    guide = (rgb_u8.astype(np.float32) / 255.0) @ _LUMA
    return compose_mask(probs, hw, [_SKY_CLASS], guide, refine, fill_holes)

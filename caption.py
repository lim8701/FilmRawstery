# -*- coding: utf-8 -*-
"""사진 영어 캡션 생성 — Florence-2-base-ft ONNX (MIT).

RAF 내장 JPEG 프리뷰(호출측이 768x768 RGB 로 축소해 전달)를 입력으로
영어 캡션 문장을 생성한다. numpy + onnxruntime 만 사용(Qt 독립).

- 모델: onnx-community/Florence-2-base-ft (fp32 4파일 ~1.1GB) — git 미커밋,
  최초 사용 시 ensure_model() 이 Hugging Face 에서 자동 다운로드(원자적 tmp→rename).
  파일명은 전부 `florence2_` 접두사(.gitignore 의 `models/florence2_*` 커버).
- 토크나이저: vocab.json + merges.txt 의 GPT-2식 byte-level BPE 직접 구현
  (BART 토크나이저 호환, 의존성 추가 없음).
- 생성: greedy, 무캐시 디코더(짧은 캡션 ~15토큰이라 충분히 빠름 — 실측 CPU
  비전 ~1.3s + 캡션 ~1.4s). 추후 가속이 필요하면 decoder_model_merged(KV-cache)
  + DirectML EP(ai_denoise 패턴) 전환.
"""
import json
import os
import re
import threading
import urllib.request

import numpy as np

_REPO = "https://huggingface.co/onnx-community/Florence-2-base-ft/resolve/main"
MODEL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

# 로컬 파일명 -> repo 상대경로. onnx 4개 + 토크나이저/설정 4개.
_FILES = {
    "florence2_vision_encoder.onnx": "onnx/vision_encoder.onnx",
    "florence2_embed_tokens.onnx": "onnx/embed_tokens.onnx",
    "florence2_encoder_model.onnx": "onnx/encoder_model.onnx",
    "florence2_decoder_model.onnx": "onnx/decoder_model.onnx",
    "florence2_vocab.json": "vocab.json",
    "florence2_merges.txt": "merges.txt",
    "florence2_preprocessor_config.json": "preprocessor_config.json",
    "florence2_generation_config.json": "generation_config.json",
}
_TOTAL_BYTES = 1_090_000_000     # 진행률 표시용 대략 총량(fp32 4파일 합)

# 캡션 상세도(Florence-2 태스크) -> 프롬프트 (processing_florence2.py 의 매핑과 동일)
TASKS = {
    "<CAPTION>": "What does the image describe?",
    "<DETAILED_CAPTION>": "Describe in detail what is shown in the image.",
    "<MORE_DETAILED_CAPTION>": "Describe with a paragraph what is shown in the image.",
}
INPUT_EDGE = 768                 # 모델 입력 크기(preprocessor_config 와 동일, 고정)

_dl_lock = threading.Lock()
_sess_lock = threading.Lock()
_state = None                    # (sessions dict, Bpe, gen config) 캐시


def _path(name: str) -> str:
    return os.path.join(MODEL_DIR, name)


def is_ready() -> bool:
    """모든 모델/토크나이저 파일이 로컬에 있는지(없으면 첫 실행 시 대용량 다운로드)."""
    return all(os.path.exists(_path(n)) for n in _FILES)


def ensure_model(progress=None) -> None:
    """모델 파일 보장(없으면 다운로드, 총 ~1.1GB). progress(0..1) 콜백 옵션.
    락으로 동시 다운로드 방지. 파일별 원자적 tmp→rename(부분파일 방지)."""
    with _dl_lock:
        done = sum(os.path.getsize(_path(n)) for n in _FILES if os.path.exists(_path(n)))
        for name, rel in _FILES.items():
            dst = _path(name)
            if os.path.exists(dst):
                continue
            os.makedirs(MODEL_DIR, exist_ok=True)
            tmp = dst + ".part"
            with urllib.request.urlopen(f"{_REPO}/{rel}") as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(min(1.0, done / _TOTAL_BYTES))
            os.replace(tmp, dst)
        if progress is not None:
            progress(1.0)


# ---------------- GPT-2 byte-level BPE (BART 토크나이저 호환) ----------------
def _bytes_to_unicode():
    bs = (list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1))
          + list(range(ord("®"), ord("ÿ") + 1)))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, map(chr, cs)))


class _Bpe:
    # 프롬프트는 ASCII 고정 문장 → GPT-2 정규식의 ASCII 근사로 충분
    _PAT = re.compile(
        r"'s|'t|'re|'ve|'m|'ll|'d| ?[A-Za-z]+| ?[0-9]+| ?[^\sA-Za-z0-9]+|\s+(?!\S)|\s+")

    def __init__(self, vocab_path, merges_path):
        with open(vocab_path, encoding="utf-8") as f:
            self.enc = json.load(f)
        self.dec = {v: k for k, v in self.enc.items()}
        with open(merges_path, encoding="utf-8") as f:
            merges = [ln for ln in f.read().split("\n") if ln and not ln.startswith("#")]
        self.ranks = {tuple(m.split()): i for i, m in enumerate(merges)}
        self.b2u = _bytes_to_unicode()
        self.u2b = {v: k for k, v in self.b2u.items()}
        self.cache = {}

    def _bpe(self, tok):
        if tok in self.cache:
            return self.cache[tok]
        word = list(tok)
        while len(word) > 1:
            pairs = {(word[i], word[i + 1]) for i in range(len(word) - 1)}
            best = min(pairs, key=lambda p: self.ranks.get(p, 1 << 30))
            if best not in self.ranks:
                break
            a, b = best
            out, i = [], 0
            while i < len(word):
                if i < len(word) - 1 and word[i] == a and word[i + 1] == b:
                    out.append(a + b)
                    i += 2
                else:
                    out.append(word[i])
                    i += 1
            word = out
        self.cache[tok] = word
        return word

    def encode(self, text):
        ids = []
        for tok in self._PAT.findall(text):
            u = "".join(self.b2u[b] for b in tok.encode("utf-8"))
            ids.extend(self.enc[p] for p in self._bpe(u))
        return ids

    def decode(self, ids, skip=()):
        parts = [self.dec.get(int(i)) for i in ids
                 if i not in skip and self.dec.get(int(i)) is not None]
        data = bytes(self.u2b.get(ch, ord(" ")) for ch in "".join(parts))
        return data.decode("utf-8", errors="replace")


def _load_state():
    """세션/토크나이저 lazy 로드(1회, ~3s). 이후 호출은 캐시 재사용."""
    global _state
    with _sess_lock:
        if _state is not None:
            return _state
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.log_severity_level = 3
        prov = ["CPUExecutionProvider"]

        def sess(name):
            return ort.InferenceSession(_path(name), opts, providers=prov)

        sessions = {
            "vis": sess("florence2_vision_encoder.onnx"),
            "emb": sess("florence2_embed_tokens.onnx"),
            "enc": sess("florence2_encoder_model.onnx"),
            "dec": sess("florence2_decoder_model.onnx"),
        }
        bpe = _Bpe(_path("florence2_vocab.json"), _path("florence2_merges.txt"))
        with open(_path("florence2_generation_config.json"), encoding="utf-8") as f:
            gen = json.load(f)
        _state = (sessions, bpe, gen)
        return _state


def generate(rgb: np.ndarray, task: str = "<CAPTION>", max_new_tokens: int = 120) -> str:
    """768x768x3 uint8 RGB(정방향) -> 영어 캡션 문장.

    호출측(main.py)이 내장 JPEG 를 EXIF 회전 반영 후 768x768 로 축소해 전달
    (IgnoreAspectRatio — Florence-2 processor 와 동일하게 비율 무시 스케일).
    """
    if rgb.shape != (INPUT_EDGE, INPUT_EDGE, 3):
        raise ValueError(f"expected {INPUT_EDGE}x{INPUT_EDGE}x3, got {rgb.shape}")
    prompt = TASKS[task]
    sessions, bpe, gen = _load_state()
    vis, emb, enc, dec = (sessions[k] for k in ("vis", "emb", "enc", "dec"))
    bos = gen.get("bos_token_id", 0)
    eos = gen.get("eos_token_id", 2)
    pad = gen.get("pad_token_id", 1)
    dec_start = gen.get("decoder_start_token_id", 2)

    # 전처리: ImageNet 정규화 (preprocessor_config 기본값)
    arr = rgb.astype(np.float32) / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], np.float32)) \
        / np.array([0.229, 0.224, 0.225], np.float32)
    px = arr.transpose(2, 0, 1)[None]

    img_feat = vis.run(None, {"pixel_values": px})[0]
    ids = [bos] + bpe.encode(prompt) + [eos]
    txt_emb = emb.run(None, {"input_ids": np.array([ids], np.int64)})[0]
    merged = np.concatenate([img_feat, txt_emb], axis=1).astype(np.float32)
    mask = np.ones(merged.shape[:2], np.int64)
    enc_out = enc.run(None, {"inputs_embeds": merged, "attention_mask": mask})[0]

    dec_ids = [dec_start]
    for _ in range(max_new_tokens):
        d_emb = emb.run(None, {"input_ids": np.array([dec_ids], np.int64)})[0]
        logits = dec.run(None, {"inputs_embeds": d_emb,
                                "encoder_hidden_states": enc_out,
                                "encoder_attention_mask": mask})[0]
        nxt = int(np.argmax(logits[0, -1]))
        dec_ids.append(nxt)
        if nxt == eos and len(dec_ids) > 2:
            break
    return bpe.decode(dec_ids, skip={bos, eos, pad, dec_start}).strip()

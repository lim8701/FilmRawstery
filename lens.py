"""후지 RAF 내장 렌즈 보정 — 파일이 담고 있는 샷별 보정 테이블(FujiIFD)을 파싱해 적용.

후지 카메라는 RAF 안에 그 샷의 렌즈 보정 파라미터를 기록한다(초점/조리개 반영, 바디+렌즈 무관):
  FujiIFD(메타 블록 뒤 II* TIFF, 서브IFD 0xf000)
    0xf00b GeometricDistortionParams  [상수, 노트×n, 왜곡%×n]
    0xf00f ChromaticAberrationParams  [상수, 노트×n, R배율차×n, B배율차×n, (상수)]
    0xf010 VignettingParams           [상수, 노트×n, 잔존광%×n]
이를 그대로 적용하므로 **후지 전 기종**(고정/교환 렌즈)이 기종 등록 없이 지원된다.
(과거의 X100V 하드코딩 눈대중 프로파일을 대체 — X100V 자신도 이 태그를 기록하며,
왜곡=0·CA/비네팅은 샷별 값이라 이쪽이 카메라/라이트룸 현상과 정합.)

해석은 RawTherapee lensmetadata.cc 와 동일(샘플 RAF 실측으로 구조 검증):
  - 노트 = 코너=1.0 정규화 반경(√(i/8) 간격 + 여유), 선형 보간·경계 클램프
  - 왜곡: m(r_src)=1+v/100, r_dst=r_src/m → 역테이블로 dest→src 리매핑
  - 비네팅: gain=100/v (선형광 기준 → 감마 인코딩 입력에는 gain^(1/2.4) 적용)
  - CA: R/B 채널 반경 배율 1+val(반경 의존)
태그가 없거나 파싱 실패 시 None → 보정 없음(항등). 비후지/구형 RAF 도 안전.
"""

import struct

import numpy as np
from scipy.ndimage import map_coordinates

_RAF_MAGIC = b"FUJIFILMCCD-RAW "
_PROFILE_CACHE = {}   # path -> (size, mtime, profile|None)
_COORD_CACHE = {}     # (h, w, sig) -> (coords3|None, gain|None)

_TAG_DIST, _TAG_CA, _TAG_VIG = 0xF00B, 0xF00F, 0xF010


def _rationals(buf, off, cnt):
    a = np.frombuffer(buf, dtype="<i4", count=cnt * 2, offset=off).reshape(-1, 2).astype(np.float64)
    den = np.where(a[:, 1] == 0.0, 1.0, a[:, 1])
    return a[:, 0] / den


def _walk_ifd(buf, ifd, out, depth=0):
    """FujiIFD(LE TIFF) 를 걸어 렌즈 태그의 (count, offset) 수집. 오프셋은 TIFF base 상대."""
    if depth > 3 or ifd + 2 > len(buf):
        return
    n = struct.unpack_from("<H", buf, ifd)[0]
    for i in range(n):
        e = ifd + 2 + 12 * i
        if e + 12 > len(buf):
            return
        tag, typ, cnt = struct.unpack_from("<HHI", buf, e)
        val = struct.unpack_from("<I", buf, e + 8)[0]
        if typ == 13:                                   # sub-IFD 포인터
            _walk_ifd(buf, val, out, depth + 1)
        elif typ == 10 and tag in (_TAG_DIST, _TAG_CA, _TAG_VIG):
            out[tag] = (cnt, val)


def _knots_vals(v, n):
    """[상수, 노트×n, 값×n(, ...)] 배열에서 (노트, 값) 추출 + 노트 오름차순 검증."""
    knots, vals = v[1:1 + n], v[1 + n:1 + 2 * n]
    if len(knots) == n and len(vals) == n and np.all(np.diff(knots) > 0) \
            and knots[0] >= 0.0 and knots[-1] < 3.0:
        return knots, vals
    return None, None


def load_profile(path):
    """RAF 에서 렌즈 보정 프로파일 파싱. 태그 없음/실패 시 None(보정 안 함). 경로별 캐시."""
    import os
    try:
        st = os.stat(path)
        cached = _PROFILE_CACHE.get(path)
        if cached is not None and cached[0] == st.st_size and cached[1] == st.st_mtime:
            return cached[2]
        prof = _parse(path)
        _PROFILE_CACHE[path] = (st.st_size, st.st_mtime, prof)
        return prof
    except Exception:
        return None


def _parse(path):
    try:
        with open(path, "rb") as f:
            head = f.read(100)
            if len(head) < 100 or head[:16] != _RAF_MAGIC:
                return None
            meta_off, meta_len = struct.unpack(">II", head[92:100])
            base = meta_off + meta_len
            f.seek(base)
            buf = f.read(256 * 1024)                    # FujiIFD + 배열은 base 근처 수 KB
        if buf[:4] != b"II*\x00":
            return None
        tags = {}
        _walk_ifd(buf, struct.unpack_from("<I", buf, 4)[0], tags)
        if not tags:
            return None

        prof = {}
        if _TAG_DIST in tags:
            cnt, off = tags[_TAG_DIST]
            if cnt >= 5 and (cnt - 1) % 2 == 0:
                k, v = _knots_vals(_rationals(buf, off, cnt), (cnt - 1) // 2)
                if k is not None:
                    prof["dk"], prof["dv"] = k, v
        if _TAG_VIG in tags:
            cnt, off = tags[_TAG_VIG]
            if cnt >= 5 and (cnt - 1) % 2 == 0:
                k, v = _knots_vals(_rationals(buf, off, cnt), (cnt - 1) // 2)
                if k is not None and np.all(v > 1.0):   # 잔존광 %(0..100] 만 유효
                    prof["vk"], prof["vv"] = k, v
        if _TAG_CA in tags:
            cnt, off = tags[_TAG_CA]
            n = (cnt - 2) // 3 if (cnt - 2) % 3 == 0 else \
                ((cnt - 1) // 3 if (cnt - 1) % 3 == 0 else 0)
            if n >= 2:
                v = _rationals(buf, off, cnt)
                k, r = _knots_vals(v, n)
                if k is not None:
                    prof["cak"], prof["car"] = k, r
                    prof["cab"] = v[1 + 2 * n:1 + 3 * n]
        if not prof:
            return None
        # 좌표 캐시 키(값 자체로 — 같은 렌즈·설정이면 파일이 달라도 캐시 적중)
        prof["sig"] = tuple(np.round(np.concatenate([np.asarray(prof[k], np.float64)
                                                     for k in sorted(prof)]), 9))
        return prof
    except Exception:
        return None


# 리맵 좌표는 (해상도, 프로파일 값)에만 의존 → 캐시. 프록시는 2560 고정이라 대부분 적중.
def _coords_for(h, w, prof):
    key = (h, w, prof["sig"])
    cached = _COORD_CACHE.get(key)
    if cached is not None:
        return cached

    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx = xs - cx
    dy = ys - cy
    rn = np.sqrt(dx * dx + dy * dy).astype(np.float32) / np.float32(np.hypot(cx, cy))  # 코너=1

    # 왜곡: dest 반경 → src 반경 역테이블 (s = r_src/r_dst 리맵 스케일)
    if "dk" in prof and np.any(np.abs(prof["dv"]) > 1e-9):
        rs = np.linspace(0.0, 1.3, 131)
        m = 1.0 + np.interp(rs, prof["dk"], prof["dv"]) / 100.0
        rd = rs / m
        # 채움 스케일: 배럴 보정(m>1)은 dest 코너(rn=1)가 src 코너 밖(r_src>1)을
        # 요구 → mode="nearest" 클램프로 가장자리 픽셀이 방사형으로 번짐(sweep).
        # dest 반경을 a=rd(rs=1)(<1) 배로 축소해 코너=코너 정합 — 결과를 1/a 배
        # 확대·크롭하는 것과 동일(카메라 JPEG/라이트룸의 scale-to-fill 처리).
        # a>=1(밖을 요구하지 않는 방향)이면 그대로 둔다(빈 영역이 애초에 없음).
        fill = np.float32(min(float(np.interp(1.0, rs, rd)), 1.0))
        r_src = np.interp(rn * fill, rd, rs).astype(np.float32)
        s = np.where(rn > 1e-6, r_src / np.maximum(rn, np.float32(1e-6)),
                     np.float32(m[0]) * fill).astype(np.float32)
    else:
        r_src = rn
        s = None                                        # 왜곡 없음

    # CA: R/B 채널 반경 배율(반경 의존). 왜곡과 합성해 채널별 리맵 스케일.
    car = cab = None
    if "cak" in prof and (np.any(np.abs(prof["car"]) > 1e-9)
                          or np.any(np.abs(prof["cab"]) > 1e-9)):
        car = (1.0 + np.interp(r_src, prof["cak"], prof["car"])).astype(np.float32)
        cab = (1.0 + np.interp(r_src, prof["cak"], prof["cab"])).astype(np.float32)

    coords3 = None
    if s is not None or car is not None:
        sr = car if s is None else (s if car is None else s * car)
        sg = s
        sb = cab if s is None else (s if cab is None else s * cab)
        def _c(sc):
            return None if sc is None else [cy + dy * sc, cx + dx * sc]
        coords3 = [_c(sr), _c(sg), _c(sb)]

    # 비네팅: 잔존광 % → 선형 게인 100/v. 입력이 감마(2.4) 인코딩이므로 지수 보정해 적용.
    gain = None
    if "vk" in prof:
        g = 100.0 / np.interp(r_src, prof["vk"], prof["vv"])
        gain = (g ** (1.0 / 2.4)).astype(np.float32)[..., None]

    _COORD_CACHE[key] = (coords3, gain)
    return _COORD_CACHE[key]


def apply(arr, prof):
    """arr (H,W,3) uint8/uint16/float → 보정본(같은 dtype). prof=None 이면 그대로 반환."""
    if prof is None:
        return arr
    h, w = arr.shape[:2]
    coords3, gain = _coords_for(h, w, prof)

    out = arr
    if coords3 is not None:
        out = np.empty_like(arr)
        for ch in range(3):
            if coords3[ch] is None:                     # 리맵 불필요 채널(항등)
                out[..., ch] = arr[..., ch]
            else:
                out[..., ch] = map_coordinates(arr[..., ch], coords3[ch],
                                               order=1, mode="nearest")

    # 주변광량 보정(코너 밝힘)
    if gain is not None:
        o = out.astype(np.float32) * gain
        if np.issubdtype(arr.dtype, np.integer):
            o = np.clip(o, 0, np.iinfo(arr.dtype).max)
            out = o.astype(arr.dtype)
        else:
            out = np.clip(o, 0.0, 1.0).astype(arr.dtype)
    return out

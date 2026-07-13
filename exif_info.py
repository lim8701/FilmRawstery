"""RAF 촬영정보(EXIF) 추출.

Fuji RAF 는 독자 컨테이너라 exifread 가 파일을 직접 못 읽지만, 내부에 표준 EXIF 를
가진 JPEG 프리뷰가 임베드돼 있다(헤더: 0x54=JPEG offset, 0x58=length, big-endian).
그 JPEG 만 떼어 exifread 로 표준 EXIF 를 읽는다.

주의: Fuji 필름 시뮬레이션 이름은 MakerNote 에 있어 exifread 로는 안 나온다
(앱 자체 필름시뮬 셀렉터로 대체). 여기선 카메라/노출/렌즈 등 표준 EXIF 만 다룬다.
"""
import io
import struct

try:
    import exifread
except Exception:  # 의존성 없으면 기능만 비활성(앱은 계속 동작)
    exifread = None

_RAF_MAGIC = b"FUJIFILMCCD-RAW "


def _read_embedded_jpeg(raf_path, max_bytes=512 * 1024):
    """RAF 헤더에서 임베드 JPEG 위치를 찾아 앞부분(EXIF 포함)만 읽어 반환."""
    with open(raf_path, "rb") as f:
        head = f.read(92)
        if len(head) < 92 or head[:16] != _RAF_MAGIC:
            return None
        off = struct.unpack(">I", head[84:88])[0]
        length = struct.unpack(">I", head[88:92])[0]
        if off <= 0 or length <= 0:
            return None
        f.seek(off)
        return f.read(min(length, max_bytes))  # EXIF APP1 은 JPEG 앞쪽


def read_orientation(raf_path) -> int:
    """RAF 임베드 JPEG 의 EXIF Image Orientation(1~8) 반환. 실패/없음 시 1(가로).
    날짜 스탬프를 촬영 방향(센서 가로 프레임)에 맞춰 배치하는 데 쓴다."""
    if exifread is None:
        return 1
    try:
        jpeg = _read_embedded_jpeg(raf_path)
        if not jpeg:
            return 1
        tags = exifread.process_file(io.BytesIO(jpeg), details=False)
        ori = tags.get("Image Orientation")
        v = int(ori.values[0]) if ori and ori.values else 1
        return v if v in (1, 2, 3, 4, 5, 6, 7, 8) else 1
    except Exception:
        return 1


def _ratio(v):
    try:
        return float(v.num) / float(v.den) if v.den else None
    except Exception:
        try:
            return float(v)
        except Exception:
            return None


def _first(tag):
    try:
        return tag.values[0]
    except Exception:
        return None


def _fmt_aperture(tag):
    f = _ratio(_first(tag))
    return f"f/{f:g}" if f else None


def _fmt_shutter(tag):
    r = _first(tag)
    try:
        num, den = r.num, r.den
    except Exception:
        return None
    if not den:
        return None
    if num <= 0:                                 # 0/x 등 변칙 EXIF → 표시 생략(0 나눗셈 방지)
        return None
    if num != 1 and den % num == 0:              # 2/4 같은 형태 정규화
        den, num = den // num, 1
    if num == 1:
        return f"1/{den}s"
    f = num / den
    if f >= 1:
        return f"{f:g}s"
    return f"1/{round(den / num)}s"


def _fmt_focal(tag):
    f = _ratio(_first(tag))
    return f"{f:g}mm" if f else None


def _fmt_iso(tag):
    v = _first(tag)
    return f"ISO {v}" if v is not None else None


def _fmt_ev(tag):
    f = _ratio(_first(tag))
    if f is None:
        return None
    return "0 EV" if abs(f) < 1e-6 else f"{f:+.2f} EV"


def _fmt_date(tag):
    # "2026:04:20 18:16:23" -> "2026-04-20 18:16:23"
    s = str(tag).strip()
    return s.replace(":", "-", 2) if s else None


def read_shooting_info(raf_path):
    """RAF 경로 -> (fields, summary).

    fields:  [{"label": str, "value": str}, ...]  (우측 패널용, 순서 유지)
    summary: 오버레이용 2줄 문자열 (예: "23mm  f/2.8\\n1/250s  ISO 1250")
    실패/비RAF/의존성없음 시 ([], "").
    """
    if exifread is None:
        return [], ""
    try:
        jpeg = _read_embedded_jpeg(raf_path)
        if not jpeg:
            return [], ""
        tags = exifread.process_file(io.BytesIO(jpeg), details=False)
    except Exception:
        return [], ""
    if not tags:
        return [], ""

    def t(key):
        return tags.get(key)

    make = str(t("Image Make") or "").strip()
    model = str(t("Image Model") or "").strip()
    camera = (make + " " + model).strip() or None

    aperture = _fmt_aperture(t("EXIF FNumber")) if t("EXIF FNumber") else None
    shutter = _fmt_shutter(t("EXIF ExposureTime")) if t("EXIF ExposureTime") else None
    iso = _fmt_iso(t("EXIF ISOSpeedRatings")) if t("EXIF ISOSpeedRatings") else None
    focal = _fmt_focal(t("EXIF FocalLength")) if t("EXIF FocalLength") else None
    ev = _fmt_ev(t("EXIF ExposureBiasValue")) if t("EXIF ExposureBiasValue") else None
    program = str(t("EXIF ExposureProgram")).strip() if t("EXIF ExposureProgram") else None
    metering = str(t("EXIF MeteringMode")).strip() if t("EXIF MeteringMode") else None
    wb = str(t("EXIF WhiteBalance")).strip() if t("EXIF WhiteBalance") else None
    flash = str(t("EXIF Flash")).strip() if t("EXIF Flash") else None
    firmware = str(t("Image Software")).strip() if t("Image Software") else None
    date = _fmt_date(t("EXIF DateTimeOriginal") or t("Image DateTime"))

    rows = [
        ("Camera", camera),
        ("Firmware", firmware),
        ("Aperture", aperture),
        ("Shutter", shutter),
        ("ISO", iso),
        ("Focal Length", focal),
        ("Exp. Comp.", ev),
        ("Program", program),
        ("Metering", metering),
        ("White Balance", wb),
        ("Flash", flash),
        ("Date", date),
    ]
    fields = [{"label": k, "value": v} for k, v in rows if v]

    # 오버레이 요약(2줄): 초점/조리개 · 셔터/ISO
    line1 = "  ".join(x for x in (focal, aperture) if x)
    line2 = "  ".join(x for x in (shutter, iso) if x)
    summary = "\n".join(x for x in (line1, line2) if x)

    return fields, summary

# -*- coding: utf-8 -*-
"""AI 캡션 문장 -> 이미지 해시태그(#) 변환 — 순수 텍스트 처리(의존성/Qt 독립).

caption.py 가 만든 영어 캡션 문장의 '주요 단어'만 뽑아 표시용 해시태그 문자열을 만든다.
별도 모델·다운로드·상태 저장 없음(캡션의 순수 파생물). 흔한 영어 기능어(관사/전치사/접속사/
대명사/be동사 등)와 짧은 토큰을 걸러 내용어만 남긴다. 숫자는 무시 — 사람 수 등 '세기'는 이
크기 VLM 의 알려진 약점이라(caption.py:16-18) 태그로 노출하지 않는 편이 안전하다.
"""
import re

# 캡션에서 태그 가치가 없는 흔한 기능어. 짧은 캡션 어휘가 단순해 이 정도로 충분하다.
_STOP = {
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "there",
    "from", "into", "onto", "over", "under", "near", "next", "out", "off",
    "his", "her", "its", "their", "them", "they", "you", "your", "our", "who",
    "has", "have", "had", "been", "being", "does", "did", "will", "would",
    "can", "could", "should", "some", "any", "all", "each", "other", "such",
    "than", "then", "very", "more", "most", "much", "many", "few",
    "what", "which", "when", "where", "while", "here", "about", "above", "below",
    "front", "back", "side", "top", "left", "right", "middle", "center",
    "image", "photo", "picture", "shows", "showing", "featuring",
}


def keywords(text: str, max_tags: int = 0) -> list:
    """영어 캡션 문장 -> 내용어 키워드 리스트(소문자, 불용어/3글자미만/숫자/중복 제거).
    표시용은 max_tags=10 으로 상위 N개 제한, 검색용은 0(무제한). 해시태그·검색이 이 단어
    선택 규칙을 공유한다(검색 대상 = 해시태그 기준의 내용어)."""
    if not text:
        return []
    seen = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) < 3 or w in _STOP or w in seen:
            continue
        seen.append(w)
        if max_tags and len(seen) >= max_tags:
            break
    return seen


def from_caption(text: str, max_tags: int = 10) -> str:
    """영어 캡션 문장 -> '#word #word ...' 표시용 문자열(빈/무효 입력이면 '')."""
    return " ".join("#" + w for w in keywords(text, max_tags))

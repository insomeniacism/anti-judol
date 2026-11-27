"""Curated Indonesian OSINT keywords and patterns for illegal site discovery."""

from __future__ import annotations

import re

# High-signal keywords for crt.sh & URLScan.io queries
DEFAULT_HARVEST_KEYWORDS: list[str] = [
    # Judol / Slot / Togel
    "slotgacor",
    "gacor",
    "maxwin",
    "togel",
    "judol",
    "slot88",
    "toto4d",
    "rtpslot",
    "pragmatic",
    "sbobet",
    "mahjongways",
    "zeus138",
    # Pinjol Ilegal
    "pinjol",
    "pinjamanonline",
    "danacepat",
    # Phishing / Scam Indonesia
    "danakaget",
    "bansos",
    "undianbri",
]

# Fast compiled regex for real-time Certstream domain filtering
CERTSTREAM_PATTERN = re.compile(
    r"(?:slot|gacor|maxwin|togel|judol|judi|toto\d*|sbobet|rtp|"
    r"zeus\d+|mahjong|scatter|pinjol|danakaget|dana-kaget|caircepat)",
    re.IGNORECASE,
)

# ------------------------------------------------------------------------------
# Keyword pre-filter (classifier.keyword_prefilter): putus kasus judol yang sangat
# jelas tanpa memanggil LLM, supaya kuota free-tier dipakai untuk kasus ambigu saja.
# ------------------------------------------------------------------------------
JUDOL_STRONG_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in (
        r"slot\s*gacor",
        r"\bgacor\b",
        r"max\s*win",
        r"\btogel\b",
        r"\btoto\s*\d*\b",
        r"\brtp\b",
        r"\bscatter\b",
        r"bonus\s*new\s*member",
        r"deposit\s*(?:pulsa|dana|ovo|gopay|qris|via)",
        r"slot\s*(?:online|terpercaya|resmi|88|777|deposit)",
        r"situs\s*(?:slot|judi|togel)",
        r"judi\s*(?:online|bola|slot)",
        r"link\s*alternatif",
        r"\bbandar\b",
        r"\bsbobet\b",
        r"sabung\s*ayam",
        r"casino\s*online",
        r"live\s*casino",
        r"pragmatic\s*play",
        r"mahjong\s*ways",
        r"\bzeus\b",
        r"olympus",
        r"\bagen\s*(?:slot|judi|togel|bola)",
        r"\bwd\b",
        r"\bjackpot\b",
    )
]

JUDOL_ACTION_PATTERN = re.compile(
    r"\b(?:daftar|login|masuk|deposit|withdraw|bonus|promo|klaim|main\s*sekarang)\b"
)

# Konteks berita / edukasi / penegakan hukum -> jangan diputus otomatis, serahkan ke LLM
JUDOL_NEGATIVE_PATTERN = re.compile(
    r"\b(?:berita|pemberantasan|berantas|komdigi|kominfo|polri|polisi|polda|polres|"
    r"ditangkap|tersangka|diblokir|bahaya\s*judi|kecanduan|waspada|imbauan|"
    r"satgas|ojk|kompas|detik|tribun|liputan6|cnn|kumparan|wikipedia)\b"
)

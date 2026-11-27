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

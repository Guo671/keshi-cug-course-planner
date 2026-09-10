"""Detect teaching-class component labels without guessing their relationship."""

import re
from collections.abc import Iterable


def needs_component_review(codes: Iterable[str]) -> bool:
    return any(re.fullmatch(r"[\u4e00-\u9fff]*\d{4,5}[A-Za-z]", code.strip()) for code in codes)

"""Score sampled EPUB text into up to three closed-set genre labels."""

from __future__ import annotations

import re
from dataclasses import dataclass

from exlibris.description_text import plain_text_description
from exlibris.epub_validate import iter_spine_xhtml
from exlibris.genre_lexicons import (
    EXPLICIT_TERMS,
    FICTION_STYLE_TERMS,
    GENRE_LEXICONS,
    NONFICTION_STYLE_TERMS,
)
from exlibris.genres import (
    EROTICA_LABEL,
    FICTION_FALLBACK,
    FICTION_FAMILY,
    MAX_GENRE_LABELS,
    NONFICTION_FALLBACK,
    NONFICTION_FAMILY,
    format_genre_labels,
)

DEFAULT_ADULT_THRESHOLD = 12.0
SAMPLE_MAX_CHARS = 70_000
WINDOW_FRACTIONS = (0.2, 0.4, 0.6, 0.8)
MIN_SCORE = 1.2
CLEAR_MARGIN = 1.75
CLOSE_RATIO = 0.6
CROSS_FAMILY_PENALTY = 0.35
_WORD_RE = re.compile(r"[A-Za-z']+")
_QUOTE_RE = re.compile(r"[\"“”]")


def _compile_terms(terms: tuple[str, ...]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for term in terms:
        if re.search(r"[^A-Za-z']", term):
            pattern = re.escape(term)
        else:
            pattern = r"\b" + re.escape(term) + r"\b"
        compiled.append(re.compile(pattern, re.IGNORECASE))
    return compiled


_GENRE_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    label: _compile_terms(terms) for label, terms in GENRE_LEXICONS.items()
}
_EXPLICIT_PATTERNS = _compile_terms(EXPLICIT_TERMS)
_FICTION_STYLE_PATTERNS = _compile_terms(FICTION_STYLE_TERMS)
_NONFICTION_STYLE_PATTERNS = _compile_terms(NONFICTION_STYLE_TERMS)


def html_to_sample_text(markup: str) -> str:
    return plain_text_description(markup) or ""


def sample_epub_text(path, *, max_chars: int = SAMPLE_MAX_CHARS) -> str:
    documents = [
        html_to_sample_text(chunk)
        for chunk in iter_spine_xhtml(path)
    ]
    documents = [doc for doc in documents if doc]
    if not documents:
        return ""
    usable = [doc for doc in documents if len(doc) > 80] or documents
    count = len(usable)
    indexes = sorted(
        {
            min(count - 1, max(0, int(count * fraction)))
            for fraction in WINDOW_FRACTIONS
        }
    )
    per_window = max(1, max_chars // max(len(indexes), 1))
    parts = [usable[index][:per_window] for index in indexes]
    return " ".join(parts)[:max_chars]


def _word_count(text: str) -> int:
    return max(len(_WORD_RE.findall(text)), 1)


def _hit_count(text: str, patterns: list[re.Pattern[str]]) -> int:
    return sum(len(pattern.findall(text)) for pattern in patterns)


def _per_thousand(hits: int, words: int) -> float:
    return hits * 1000.0 / words


def explicit_density(text: str) -> float:
    if not text.strip():
        return 0.0
    return _per_thousand(_hit_count(text, _EXPLICIT_PATTERNS), _word_count(text))


def _style_family(text: str) -> str | None:
    words = _word_count(text)
    quotes = len(_QUOTE_RE.findall(text))
    fiction = _per_thousand(
        _hit_count(text, _FICTION_STYLE_PATTERNS) + quotes,
        words,
    )
    nonfiction = _per_thousand(_hit_count(text, _NONFICTION_STYLE_PATTERNS), words)
    if fiction >= nonfiction * 1.15 and fiction >= 2.0:
        return "fiction"
    if nonfiction >= fiction * 1.15 and nonfiction >= 1.5:
        return "nonfiction"
    if fiction > nonfiction and fiction >= 2.0:
        return "fiction"
    if nonfiction > fiction and nonfiction >= 1.5:
        return "nonfiction"
    return None


def _score_genres(text: str) -> dict[str, float]:
    words = _word_count(text)
    scores: dict[str, float] = {}
    for label, patterns in _GENRE_PATTERNS.items():
        hits = _hit_count(text, patterns)
        if hits:
            scores[label] = _per_thousand(hits, words)
    return scores


def _prefer_family(scores: dict[str, float], family: str | None) -> dict[str, float]:
    if family is None:
        return scores
    allowed = FICTION_FAMILY if family == "fiction" else NONFICTION_FAMILY
    adjusted: dict[str, float] = {}
    for label, score in scores.items():
        if label in allowed:
            adjusted[label] = score
        else:
            adjusted[label] = score * CROSS_FAMILY_PENALTY
    return adjusted


def _pick_labels(scores: dict[str, float], family: str | None) -> list[str]:
    ranked = sorted(
        ((label, score) for label, score in scores.items() if score >= MIN_SCORE),
        key=lambda item: (-item[1], item[0]),
    )
    if not ranked:
        if family == "fiction":
            return [FICTION_FALLBACK]
        if family == "nonfiction":
            return [NONFICTION_FALLBACK]
        return []

    top_label, top_score = ranked[0]
    if len(ranked) == 1 or top_score >= ranked[1][1] * CLEAR_MARGIN:
        chosen = [top_label]
    else:
        threshold = top_score * CLOSE_RATIO
        chosen = [label for label, score in ranked if score >= threshold][:MAX_GENRE_LABELS]

    specific = [label for label in chosen if label not in {FICTION_FALLBACK, NONFICTION_FALLBACK}]
    if specific:
        chosen = specific[:MAX_GENRE_LABELS]
    return chosen[:MAX_GENRE_LABELS]


def _append_erotica(labels: list[str], family: str | None) -> list[str]:
    if family != "fiction" or EROTICA_LABEL in labels:
        return labels[:MAX_GENRE_LABELS]
    if len(labels) < MAX_GENRE_LABELS:
        return labels + [EROTICA_LABEL]
    return labels[:-1] + [EROTICA_LABEL]


def _english_enough(language: str | None) -> bool:
    if not language:
        return True
    token = language.strip().lower().replace("_", "-")
    return token == "en" or token.startswith("en-") or token.startswith("eng")


@dataclass(frozen=True)
class Classification:
    labels: list[str]
    family: str | None
    adult_density: float
    scores: dict[str, float]

    @property
    def genre(self) -> str | None:
        return format_genre_labels(self.labels)


def classify_text(
    sample: str,
    *,
    title: str | None = None,
    description: str | None = None,
    language: str | None = None,
    adult_threshold: float = DEFAULT_ADULT_THRESHOLD,
) -> Classification:
    title_text = title or ""
    description_text = description or ""
    bag = " ".join(
        part for part in (title_text, title_text, description_text, description_text, sample) if part
    )
    family = _style_family(bag)
    scores = _prefer_family(_score_genres(bag), family)
    labels = _pick_labels(scores, family)
    density = explicit_density(sample) if sample.strip() else explicit_density(bag)
    if (
        labels
        and (family == "fiction" or any(label in FICTION_FAMILY for label in labels))
        and _english_enough(language)
        and density >= adult_threshold
    ):
        overlay_family = family or "fiction"
        labels = _append_erotica(labels, overlay_family)
    return Classification(
        labels=labels,
        family=family,
        adult_density=density,
        scores=scores,
    )


def classify_epub(
    path,
    *,
    title: str | None = None,
    description: str | None = None,
    language: str | None = None,
    adult_threshold: float = DEFAULT_ADULT_THRESHOLD,
) -> Classification:
    sample = sample_epub_text(path)
    return classify_text(
        sample,
        title=title,
        description=description,
        language=language,
        adult_threshold=adult_threshold,
    )

"""Cleaning, chapter splitting and sentence segmentation."""

import re

import pysbd

GUTENBERG_START = re.compile(r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG.*$", re.M)
GUTENBERG_END = re.compile(r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG.*$", re.M)


class PrepError(Exception):
    pass


def strip_gutenberg(raw: str) -> str:
    """Normalise line endings and keep only the text between the Gutenberg markers."""
    text = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    if start := GUTENBERG_START.search(text):
        text = text[start.end():]
    if end := GUTENBERG_END.search(text):
        text = text[: end.start()]
    # Older files open with a credits paragraph ("Produced by …").
    text = re.sub(r"\A\s*Produced by\b.*?(\n\s*\n|\Z)", "\n", text, flags=re.S)
    return text


def remove_patterns(text: str, patterns: list[str]) -> str:
    """Delete front matter repeated inside the text (e.g. the title block at the
    top of each part of a volume Gutenberg splits into several ebooks)."""
    for pat in patterns:
        text = re.sub(pat, "\n", text, flags=re.M)
    return text


def split_chapters(text: str, specs: list[dict], lang: str) -> list[str]:
    """Cut the text at each chapter heading; returns one raw body per chapter."""
    starts = []
    pos = 0
    for n, spec in enumerate(specs, 1):
        pattern = re.compile(spec["start"], re.M)
        match = None
        for _ in range(spec.get("nth", 1)):
            match = pattern.search(text, pos)
            if not match:
                raise PrepError(f"[{lang}] chapter {n} ({spec.get('title')!r}): "
                                f"heading /{spec['start']}/ not found after offset {pos}")
            pos = match.end()
        # include: the match is the chapter's first words, not a heading to drop.
        body_start = match.start() if spec.get("include") else match.end()
        starts.append((match.start(), body_start))
    bodies = []
    for k, (_, body_start) in enumerate(starts):
        body_end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
        bodies.append(text[body_start:body_end])
    return bodies


def paragraphs(body: str, lang: str) -> list[str]:
    """Join hard-wrapped lines; paragraphs are separated by blank lines."""
    out = []
    for block in re.split(r"\n\s*\n", body):
        para = re.sub(r"\s+", " ", block).strip()
        # Skip separators such as "* * *" (no letters at all).
        if re.search(r"\w", para):
            out.append(normalise(para, lang))
    return out


def normalise(s: str, lang: str) -> str:
    s = s.replace(" ", " ").replace(" ", " ")
    s = re.sub(r"\.\.\.", "…", s)
    s = re.sub(r"\s*--\s*", lambda m: "—" if m.start() == 0 else " — ", s)
    # French books often put a space before ; : ! ? and inside « » — make it
    # consistent (a non-breaking thin space is restored at render time if wanted).
    s = re.sub(r"\s+([;:!?»])", r"\1", s)
    s = re.sub(r"«\s+", "«", s)
    # French quotes with « » and “ ”, so a straight ' is always an apostrophe;
    # use the typographic one, as most editions do. (Not in English, where '
    # is also a quotation mark.)
    if lang == "fr":
        s = s.replace("'", "’")
    return s.strip()


# Titles after which a full stop does not end the sentence ("Mme. Swann").
_ABBREV = re.compile(r"(?:^|[\s«\"“‘'(—])(?:M|MM|Mme|Mmes|Mlle|Mlles|Mr|Mrs|Dr|St|Ste|Mgr|[A-Z])\.$")

# Closing quotes/brackets that the segmenter sometimes leaves at the start of
# the next sentence. Straight quotes are ambiguous, so they count as closing
# only when followed by a space or the end ('…sleep." And' vs '"Let's').
_LEADING_CLOSER = re.compile(r"^(?:[»”’)\]]+|[\"'_]+(?=\s|$))")

# Extra split points the segmenter misses: inside quotations (it never splits
# a quoted speech, however long) and between consecutive dialogue lines
# («…sœurs.» «Ne commencez…»). A full stop followed by a capital or an opening
# quote ends a sentence; abbreviations are rejoined afterwards.
_EXTRA_SPLIT = re.compile(r"(?<=[.!?…])[»”\"’']?\s+(?=[«“\"‘—]|[A-ZÀ-Ý])")


def _split_extra(s: str) -> list[str]:
    # Split keeping any closing quote with the sentence it closes.
    out, start = [], 0
    for m in _EXTRA_SPLIT.finditer(s):
        end = m.start() + len(m.group(0).rstrip())
        out.append(s[start:end])
        start = m.end()
    out.append(s[start:])
    return out


class Segmenter:
    def __init__(self, lang: str):
        self.lang = lang
        self.seg = pysbd.Segmenter(language=lang, clean=False)

    def sentences(self, para: str) -> list[str]:
        raw = [part.strip() for s in self.seg.segment(para)
               for part in _split_extra(s) if part.strip()]
        out: list[str] = []
        for s in raw:
            # Move leading closing quotes/brackets back to the previous sentence.
            m = _LEADING_CLOSER.match(s)
            if m and out:
                out[-1] += m.group(0)
                s = s[m.end():].strip()
            if not s:
                continue
            # A fragment with no letters (e.g. "—" or "…") is glued to its neighbour.
            if out and not re.search(r"\w", s):
                out[-1] += " " + s
                continue
            # A sentence that starts lowercase almost always means the segmenter
            # split after an abbreviation or an exclamation inside a sentence.
            if out and s[0].islower() and not out[-1].endswith((".", "…")):
                out[-1] += " " + s
                continue
            if out and _ABBREV.search(out[-1]):
                out[-1] += " " + s
                continue
            out.append(s)
        return out


def segment_chapter(body: str, lang: str, segmenter: Segmenter) -> list[dict]:
    """Return the chapter's sentences as [{"t": text, "p": paragraph_start}]."""
    sents = []
    for para in paragraphs(body, lang):
        for k, s in enumerate(segmenter.sentences(para)):
            sents.append({"t": s, "p": k == 0})
    if not sents:
        raise PrepError(f"[{lang}] empty chapter")
    return sents

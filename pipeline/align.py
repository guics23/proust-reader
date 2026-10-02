"""Sentence alignment between two translations of the same chapter.

Each sentence is embedded with a multilingual model (LaBSE), so a French
sentence and its English translation land close together. A dynamic program
then finds the best monotonic path through the chapter, made of "beads": a
group of 1–4 source sentences matched with 1–4 target sentences (or, as a last
resort, a sentence matched with nothing). The approach follows Bertalign
(Liu & Zhu, 2022): span vectors, a margin correction that penalises spans
which are as similar to their neighbours as to each other, and a penalty for
spans whose lengths don't match the translation's usual length ratio.
"""

import hashlib
from pathlib import Path

import numpy as np

MODEL_NAME = "sentence-transformers/LaBSE"
MAX_SPAN = 4         # at most 4 sentences on either side of a bead
MAX_BEAD = 5         # and at most 5 sentences in total (allows 1-4, 2-3, …)
SKIP = -0.1          # score of a sentence left unmatched
CHUNK_WORDS = 120    # long sentences are embedded in pieces (model truncates)
ROW_BLOCK = 256

BEAD_TYPES = [(s, t) for s in range(1, MAX_SPAN + 1) for t in range(1, MAX_SPAN + 1)
              if s + t <= MAX_BEAD]

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_NAME, device="cpu")
    return _model


def embed(sentences: list[str], cache_dir: Path, tag: str) -> np.ndarray:
    """Unit vectors, one per sentence; cached by content hash."""
    digest = hashlib.sha1("\n".join(sentences).encode()).hexdigest()[:16]
    path = cache_dir / f"emb-{tag}-{digest}.npy"
    if path.exists():
        return np.load(path)

    # Split long sentences into word chunks; a sentence's vector is the sum of
    # its chunks' vectors, so nothing past the model's token limit is lost.
    pieces, owner = [], []
    for k, s in enumerate(sentences):
        words = s.split()
        for i in range(0, max(len(words), 1), CHUNK_WORDS):
            pieces.append(" ".join(words[i:i + CHUNK_WORDS]))
            owner.append(k)
    vecs = _get_model().encode(pieces, batch_size=32, show_progress_bar=True,
                               convert_to_numpy=True, normalize_embeddings=True)
    out = np.zeros((len(sentences), vecs.shape[1]), dtype=np.float32)
    np.add.at(out, np.array(owner), vecs)
    out /= np.linalg.norm(out, axis=1, keepdims=True)

    cache_dir.mkdir(parents=True, exist_ok=True)
    for old in cache_dir.glob(f"emb-{tag}-*.npy"):
        old.unlink()
    np.save(path, out)
    return out


class _Spans:
    """Vectors and lengths of every run of 1..MAX_SPAN consecutive sentences."""

    def __init__(self, vecs: np.ndarray, lens: np.ndarray):
        # A span's vector is the length-weighted sum of its sentence vectors.
        # (Summed directly: prefix-sum differences lose precision in float32.)
        self.weighted = vecs * lens[:, None]
        self.lens = lens

    def span(self, size: int, ends: np.ndarray) -> np.ndarray:
        """Unit vectors of spans [end-size, end) for each end (ends >= size)."""
        v = sum(self.weighted[ends - 1 - k] for k in range(size))
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)

    def length(self, size: int, ends: np.ndarray) -> np.ndarray:
        return sum(self.lens[ends - 1 - k] for k in range(size))


def align(src_vecs, tgt_vecs, src_lens, tgt_lens):
    """Align two sentence sequences; returns a list of (src_range, tgt_range, score).

    Ranges are (start, end) half-open sentence index pairs.
    """
    n, m = len(src_vecs), len(tgt_vecs)
    src_lens = np.asarray(src_lens, dtype=np.float32)
    tgt_lens = np.asarray(tgt_lens, dtype=np.float32)
    S, T = _Spans(src_vecs, src_lens), _Spans(tgt_vecs, tgt_lens)
    ratio = src_lens.sum() / tgt_lens.sum()

    # Target spans for every size, indexed by end position j (1..m); rows with
    # j < size are unused and left as zeros.
    j_all = np.arange(m + 1)
    tgt_span = {}
    tgt_span_len = {}
    for t in range(1, MAX_SPAN + 1):
        ends = j_all[t:]
        v = np.zeros((m + 1, src_vecs.shape[1]), np.float32)
        v[t:] = T.span(t, ends)
        tgt_span[t] = v
        l = np.zeros(m + 1, np.float32)
        l[t:] = T.length(t, ends)
        tgt_span_len[t] = l
    # Target sentence vectors padded so index -1 and m are "no neighbour".
    tgt_pad = np.vstack([tgt_vecs, np.zeros((1, tgt_vecs.shape[1]), np.float32)])
    src_pad = np.vstack([src_vecs, np.zeros((1, src_vecs.shape[1]), np.float32)])

    NEG = np.float32(-1e9)
    cost = np.full((n + 1, m + 1), NEG, np.float32)
    back = np.zeros((n + 1, m + 1), np.int8)      # index into TYPES below
    score_of = np.zeros((n + 1, m + 1), np.float32)
    TYPES = [(0, 1), (1, 0)] + BEAD_TYPES
    cost[0] = SKIP * j_all
    back[0, 1:] = 0

    for r0 in range(1, n + 1, ROW_BLOCK):
        rows = np.arange(r0, min(r0 + ROW_BLOCK, n + 1))
        # Similarities for this block of rows, per bead type: (rows, m+1).
        block = {}
        for s, t in BEAD_TYPES:
            ok = rows >= s
            if not ok.any():
                continue
            sv = np.zeros((len(rows), src_vecs.shape[1]), np.float32)
            sv[ok] = S.span(s, rows[ok])
            sim = sv @ tgt_span[t].T                                # (rows, m+1)
            # Margin: average similarity of each side with the sentences just
            # outside the other side's span.
            j = j_all
            before_t = np.where(j - t - 1 >= 0, j - t - 1, m)       # m → zero row
            after_t = np.where(j < m, j, m)
            to_tgt = sv @ tgt_pad.T                                 # (rows, m+1)
            nb_t = (to_tgt[:, before_t] + to_tgt[:, after_t]) / 2
            before_s = np.where(rows - s - 1 >= 0, rows - s - 1, n)
            after_s = np.where(rows < n, rows, n)
            nb_s = (src_pad[before_s] @ tgt_span[t].T
                    + src_pad[after_s] @ tgt_span[t].T) / 2         # (rows, m+1)
            margin = sim - (nb_t + nb_s) / 2
            # Length penalty in [0, 1]: log2(1 + short/long).
            ls = np.zeros(len(rows), np.float32)
            ls[ok] = S.length(s, rows[ok])
            lt = tgt_span_len[t] * ratio
            lo = np.minimum(ls[:, None], lt[None, :])
            hi = np.maximum(ls[:, None], lt[None, :])
            penalty = np.log2(1 + lo / np.maximum(hi, 1))
            sc = margin * penalty
            sc[~ok] = NEG
            sc[:, :t] = NEG
            block[(s, t)] = sc

        for k, i in enumerate(rows):
            best = cost[i - 1] + SKIP                               # (1, 0)
            arg = np.full(m + 1, 1, np.int8)
            sc_best = np.full(m + 1, SKIP, np.float32)
            for a, (s, t) in enumerate(TYPES[2:], start=2):
                if (s, t) not in block or i < s:
                    continue
                sc = block[(s, t)][k]
                cand = np.full(m + 1, NEG, np.float32)
                cand[t:] = cost[i - s, :-t] + sc[t:]
                better = cand > best
                best = np.where(better, cand, best)
                arg[better] = a
                sc_best = np.where(better, sc, sc_best)
            # (0, 1) insertions run along the row: best[j] vs best[j-1] + SKIP.
            # Closed form: max_{k<=j} (best[k] + (j-k)*SKIP).
            # The pointer is decided by comparing with the left neighbour
            # directly, not with `run`, whose float rounding fakes near-ties.
            run = np.maximum.accumulate(best - SKIP * j_all) + SKIP * j_all
            left = np.empty_like(best)
            left[0] = NEG
            left[1:] = run[:-1] + SKIP
            from_left = left > best
            arg[from_left] = 0
            sc_best[from_left] = SKIP
            cost[i] = np.where(from_left, left, best)
            back[i] = arg
            score_of[i] = sc_best

    # Trace back from (n, m).
    beads = []
    i, j = n, m
    while i > 0 or j > 0:
        a = back[i, j] if i > 0 else 0
        s, t = TYPES[a]
        beads.append(((i - s, i), (j - t, j), float(score_of[i, j])))
        i, j = i - s, j - t
    beads.reverse()
    return beads

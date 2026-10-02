"""The pipeline steps. Each takes a Volume and reads/writes files under work/."""

import csv
import json
from pathlib import Path

import yaml

from .text import (PrepError, Segmenter, remove_patterns, segment_chapter, split_chapters,
                   strip_gutenberg)

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "sources"
WORK = ROOT / "work"
BOOKS = ROOT / "public" / "books"

LOW_SCORE = 0.12       # beads below this go to the review list
PAGE_MIN_CHARS = 90    # consecutive beads are grouped until a page reaches this
PAGE_MAX_CHARS = 450   # …unless grouping would make the page longer than this


class Volume:
    def __init__(self, vid: str):
        self.id = vid
        self.src = SOURCES / vid
        cfg_path = self.src / "book.yaml"
        if not cfg_path.exists():
            raise PrepError(f"{cfg_path} not found")
        self.cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        self.langs = self.cfg["languages"]
        if len(self.langs) != 2:
            raise PrepError("exactly two languages are supported")
        self.work = WORK / vid
        self.sentences_path = self.work / "sentences.json"
        self.align_dir = self.work / "align"

    def sentences(self) -> list[dict]:
        if not self.sentences_path.exists():
            raise PrepError(f"{self.sentences_path} missing — run the chapters step first")
        return json.loads(self.sentences_path.read_text(encoding="utf-8"))

    def alignment(self, k: int) -> list[tuple]:
        path = self.align_dir / f"{k + 1:02}.tsv"
        if not path.exists():
            raise PrepError(f"{path} missing — run the align step first")
        beads = []
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                beads.append((_parse_range(row["src"]), _parse_range(row["tgt"]),
                              float(row["score"])))
        return beads


def all_volumes() -> list[str]:
    vols = [p.parent.name for p in SOURCES.glob("*/book.yaml")]
    return sorted(vols, key=lambda v: (Volume(v).cfg.get("order", 0), v))


# ---------------------------------------------------------------- chapters

def step_chapters(vol: Volume):
    print(f"[{vol.id}] chapters")
    specs = vol.cfg["chapters"]
    per_lang = {}
    for lang in vol.langs:
        # A language's source may be one file or a list of files (a volume
        # Gutenberg publishes in several ebooks), joined in order.
        files = vol.cfg["sources"][lang]
        files = [files] if isinstance(files, str) else files
        text = "\n\n".join(strip_gutenberg((vol.src / f).read_text(encoding="utf-8"))
                           for f in files)
        text = remove_patterns(text, vol.cfg.get("remove", {}).get(lang, []))
        bodies = split_chapters(text, [c[lang] for c in specs], lang)
        seg = Segmenter(lang)
        per_lang[lang] = [segment_chapter(b, lang, seg) for b in bodies]

    a, b = vol.langs
    chars = {l: [sum(len(s["t"]) for s in ch) for ch in per_lang[l]] for l in vol.langs}
    overall = sum(chars[a]) / sum(chars[b])
    chapters = []
    for k, spec in enumerate(specs):
        ratio = chars[a][k] / chars[b][k]
        flag = "  <-- check the chapter headings" if abs(ratio / overall - 1) > 0.25 else ""
        print(f"  {k + 1:2}. {spec[a]['title'][:30]:30} {len(per_lang[a][k]):5} {a}"
              f" {len(per_lang[b][k]):5} {b}   length ratio {ratio:.2f}{flag}")
        chapters.append({"title": {l: spec[l]["title"] for l in vol.langs},
                         **{l: per_lang[l][k] for l in vol.langs}})
    vol.work.mkdir(parents=True, exist_ok=True)
    vol.sentences_path.write_text(json.dumps(chapters, ensure_ascii=False, indent=0),
                                  encoding="utf-8")


# ---------------------------------------------------------------- align

def _load_anchors(vol: Volume, chapters: list[dict]) -> dict[int, list[tuple[int, int]]]:
    """anchors.yaml: manual fixes. Each entry says "a page starts at these two
    sentences" (identified by a snippet of their text); the aligner then works
    separately on each side of the anchor."""
    path = vol.src / "anchors.yaml"
    out: dict[int, list[tuple[int, int]]] = {}
    if not path.exists():
        return out
    a, b = vol.langs
    for entry in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
        k = entry["chapter"] - 1
        idx = []
        for lang in (a, b):
            snippet = entry[lang]
            hits = [i for i, s in enumerate(chapters[k][lang]) if snippet in s["t"]]
            if len(hits) != 1:
                raise PrepError(f"anchors.yaml, chapter {k + 1}: {lang} snippet {snippet!r} "
                                f"matches {len(hits)} sentences (needs exactly 1)")
            idx.append(hits[0])
        out.setdefault(k, []).append(tuple(idx))
    for k, pts in out.items():
        pts.sort()
        if any(p[1] >= q[1] for p, q in zip(pts, pts[1:])):
            raise PrepError(f"anchors.yaml, chapter {k + 1}: anchors cross each other")
    return out


def step_align(vol: Volume):
    from . import align as al

    print(f"[{vol.id}] align")
    chapters = vol.sentences()
    anchors = _load_anchors(vol, chapters)
    a, b = vol.langs
    vol.align_dir.mkdir(parents=True, exist_ok=True)
    for k, ch in enumerate(chapters):
        src, tgt = [s["t"] for s in ch[a]], [s["t"] for s in ch[b]]
        sv = al.embed(src, vol.work / "cache", f"{k + 1:02}-{a}")
        tv = al.embed(tgt, vol.work / "cache", f"{k + 1:02}-{b}")
        cuts = [(0, 0)] + anchors.get(k, []) + [(len(src), len(tgt))]
        beads = []
        for (s0, t0), (s1, t1) in zip(cuts, cuts[1:]):
            if s0 == s1 and t0 == t1:
                continue
            part = al.align(sv[s0:s1], tv[t0:t1],
                            [len(x) for x in src[s0:s1]], [len(x) for x in tgt[t0:t1]])
            beads += [((p0 + s0, p1 + s0), (q0 + t0, q1 + t0), sc)
                      for (p0, p1), (q0, q1), sc in part]
        _write_tsv(vol.align_dir / f"{k + 1:02}.tsv", beads, src, tgt)
        low = sum(1 for *_, sc in beads if sc < LOW_SCORE)
        print(f"  {k + 1:2}. {len(beads)} beads, {low} below {LOW_SCORE}")


# Sentence ranges in the TSV files: "12", "12-14" (inclusive), or "-@12" for
# no sentence (an unmatched sentence on the other side), positioned before 12.

def _fmt_range(r):
    if r[0] == r[1]:
        return f"-@{r[0]}"
    return f"{r[0]}" if r[1] - r[0] == 1 else f"{r[0]}-{r[1] - 1}"


def _parse_range(s):
    if s.startswith("-@"):
        pos = int(s[2:])
        return (pos, pos)
    lo, _, hi = s.partition("-")
    return (int(lo), int(hi or lo) + 1)


def _write_tsv(path, beads, src, tgt):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["src", "tgt", "score", "src_text", "tgt_text"])
        for (p0, p1), (q0, q1), sc in beads:
            w.writerow([_fmt_range((p0, p1)), _fmt_range((q0, q1)),
                        f"{sc:.3f}", " ".join(src[p0:p1]), " ".join(tgt[q0:q1])])


# ---------------------------------------------------------------- review

def step_review(vol: Volume):
    print(f"[{vol.id}] review")
    chapters = vol.sentences()
    rows, kinds, total = [], {}, 0
    for k, ch in enumerate(chapters):
        a, b = vol.langs
        for (p0, p1), (q0, q1), sc in vol.alignment(k):
            total += 1
            kind = f"{p1 - p0}-{q1 - q0}"
            kinds[kind] = kinds.get(kind, 0) + 1
            if sc < LOW_SCORE:
                rows.append((sc, k + 1, kind,
                             " ".join(s["t"] for s in ch[a][p0:p1]),
                             " ".join(s["t"] for s in ch[b][q0:q1])))
    rows.sort()
    path = vol.work / "review.tsv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(["score", "chapter", "kind", "src_text", "tgt_text"])
        for sc, k, kind, s, t in rows:
            w.writerow([f"{sc:.3f}", k, kind, s, t])
    mix = ", ".join(f"{k} ×{v}" for k, v in sorted(kinds.items(), key=lambda x: -x[1]))
    print(f"  {total} beads ({mix})")
    print(f"  {len(rows)} ({len(rows) / total:.1%}) below {LOW_SCORE} → {path.relative_to(ROOT)}")


# ---------------------------------------------------------------- build

def _join(sents: list[dict], first_in_page: bool) -> str:
    out = ""
    for s in sents:
        if out or not first_in_page:
            out += "\n" if s["p"] else " "
        out += s["t"]
    return out


def step_build(vol: Volume):
    print(f"[{vol.id}] build")
    a, b = vol.langs
    chapters_out, pages = [], []
    base = 0                               # global index of the chapter's first src sentence
    for k, ch in enumerate(vol.sentences()):
        chapters_out.append({"title": ch["title"], "page": len(pages)})
        cur = None                         # [src_sents, tgt_sents, key]

        def flush():
            if cur:
                pages.append([_join(cur[0], True).strip(), _join(cur[1], True).strip(), cur[2]])

        for (p0, p1), (q0, q1), _ in vol.alignment(k):
            s_part, t_part = ch[a][p0:p1], ch[b][q0:q1]
            n_s = sum(len(x["t"]) for x in s_part)
            n_t = sum(len(x["t"]) for x in t_part)
            if cur is not None:
                c_s = sum(len(x["t"]) for x in cur[0])
                c_t = sum(len(x["t"]) for x in cur[1])
                one_sided = not s_part or not t_part
                short = max(c_s, c_t) < PAGE_MIN_CHARS
                fits = max(c_s + n_s, c_t + n_t) <= PAGE_MAX_CHARS
                if one_sided or (short and fits):
                    cur[0] += s_part
                    cur[1] += t_part
                    continue
                flush()
            cur = [list(s_part), list(t_part), base + p0]
        flush()
        base += len(ch[a])

    data = {
        "id": vol.id,
        "order": vol.cfg.get("order", 0),
        "author": vol.cfg.get("author", ""),
        "translator": vol.cfg.get("translator", ""),
        "title": vol.cfg["title"],
        "languages": vol.langs,
        "chapters": chapters_out,
        "pages": pages,
    }
    BOOKS.mkdir(parents=True, exist_ok=True)
    out = BOOKS / f"{vol.id}.json"
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"  {len(pages)} pages, {out.stat().st_size / 1e6:.1f} MB → {out.relative_to(ROOT)}")


def write_library():
    vols = []
    for p in sorted(BOOKS.glob("*.json")):
        if p.name == "library.json":
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        vols.append({"id": d["id"], "order": d["order"], "title": d["title"],
                     "author": d["author"], "file": p.name, "pages": len(d["pages"]),
                     "version": int(p.stat().st_mtime)})
    vols.sort(key=lambda v: (v["order"], v["id"]))
    (BOOKS / "library.json").write_text(json.dumps(vols, ensure_ascii=False, indent=1),
                                        encoding="utf-8")


COMMANDS = {
    "chapters": [step_chapters],
    "align": [step_align],
    "review": [step_review],
    "build": [step_build],
    "run": [step_chapters, step_align, step_review, step_build],
}

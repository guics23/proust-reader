# Proust Reader

A mobile-first web app for reading a book sentence by sentence in two
languages: original (French) in the top half of the screen, translation
(English) in the bottom half; swipe to turn. Goal: all volumes of *À la
recherche du temps perdu* (Gutenberg French + Scott Moncrieff English).

Two parts:

- `pipeline/` — Python tool that turns two Gutenberg plain-text files into
  one aligned JSON per volume. Full user guide: `PREPARING.md`.
- `public/` — the reader. Static HTML/CSS/vanilla JS, **no build step, no
  framework, no dependencies**. Keep it that way.

## Commands

```sh
export PATH=$HOME/.local/bin:$PATH             # uv lives here (system python has no pip)
uv run python -m pipeline run <volume-id>      # chapters → align → review → build
uv run python -m pipeline <step> <id|all>      # steps: chapters, align, review, build
python3 -m http.server -d public 8765          # serve the app locally
```

`align` on a new or re-segmented volume takes ~9 min (LaBSE embeddings on CPU);
run it in the background. With cached embeddings it takes seconds.

## Layout

```
sources/<id>/{fr.txt,en.txt,book.yaml,anchors.yaml?}   inputs (committed)
work/<id>/sentences.json, align/NN.tsv, review.tsv     intermediate, human-readable
work/<id>/cache/emb-*.npy                              embeddings, keyed by content hash (ignored)
public/books/<id>.json, library.json                   build output read by the app
```

- `pipeline/text.py` — Gutenberg stripping, chapter split (regex headings
  from book.yaml), paragraph unwrapping, sentence segmentation (pysbd + our
  fix-ups for quotes, dialogue, abbreviations like "Mme." / "M.").
- `pipeline/align.py` — our own Bertalign-style aligner (Bertalign itself is
  GPL and needs googletrans/faiss/numba; don't add it back). LaBSE vectors,
  spans of up to 4 sentences, margin scoring + length penalty, row-vectorised
  DP in numpy.
- `pipeline/steps.py` — the steps, anchors, page grouping, library.json.
- `public/app.js` — routing (`#<volume-id>`, no hash = library page), swipe,
  progress, bookmarks, chapter/bookmark/book sheet, font size, theme.

## Data format

Volume JSON: `{id, order, author, translator, title:{fr,en}, languages:[top,bottom],
chapters:[{title:{fr,en}, page}], pages:[[top_text, bottom_text, key]]}`.
Page text uses `\n` for paragraph breaks and `_x_` for italics (rendered as `<em>`).

`key` = global index of the page's first French sentence. **Positions and
bookmarks in localStorage (`reader.v1`) are stored by key, not page index**,
so regrouping pages (build tweaks, anchors) keeps them valid, but any change
to French segmentation or cleaning shifts them. Treat segmentation changes as
breaking for existing readers once the app is in use.

## Gotchas learned the hard way

- **float32 precision in align.py.** Two bugs came from it: prefix-sum span
  vectors (fixed: sum the ≤4 vectors directly) and the closed-form insertion
  scan faking ties (fixed: compare with the left neighbour). Symptom of both:
  obvious 1-1 pairs coming out as a `1-0` + `0-1` pair, often periodically.
  After touching the DP, check the bead-type mix: `0-1`/`1-0` should be rare
  (Swann's Way: 3 out of 4,322).
- **pysbd never splits inside quotes**, which glued whole English speeches
  into one sentence; `_EXTRA_SPLIT` in text.py handles this. Straight `"`
  is both opener and closer — see `_LEADING_CLOSER`.
- Low review scores are usually free translation (Scott Moncrieff replaces
  verse, adds or drops short phrases), not slips. A *run* of low scores
  is a slip → add an anchor in `sources/<id>/anchors.yaml`.
- Text that matches the Gutenberg structure: the English TOC repeats chapter
  headings (`nth: 2`); "Overture" (en) = "Combray I" (fr).
- `pkill -f "pipeline align"` kills its own shell too; use `pgrep` + PID.

## App conventions

- UI strings are in French.
- Colours are CSS variables on `:root` with dark/sepia variants; use them.
- The service worker (`public/sw.js`) is network-first for the app shell
  (cache is the offline fallback), so edits show on reload. Bump `CACHE` when
  changing the list of shell files. Book JSON is cached by the `?v=` version
  from library.json, so rebuilt volumes refresh automatically.
- Test in Chrome via the chrome-devtools MCP with a phone viewport
  (`390x844x3,mobile,touch`); swipes can be simulated by dispatching
  PointerEvents on `#reader`.

## Status

- Done: volume 1 (`1-swann`) aligned and built; no anchors needed.
- Next: other volumes. Check Gutenberg availability first. The English
  *Time Regained* is a different translator (Stephen Hudson) and may only be
  on Gutenberg Australia.

## Deployment

GitHub Pages via `.github/workflows/pages.yml`: every push to `main` that
touches `public/` publishes that folder as-is. All app paths are relative, so
it works under `https://<user>.github.io/<repo>/`; keep them relative.
Built volumes (`public/books/*.json`) are committed, since Pages serves them.

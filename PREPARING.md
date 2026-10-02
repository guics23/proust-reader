# Preparing a volume

This turns two Project Gutenberg plain-text files — the original and a
translation — into one `public/books/<id>.json` file the reader can open.

```
sources/<id>/fr.txt, en.txt, book.yaml   ← you provide these
        │  chapters   split into chapters and sentences
        ▼
work/<id>/sentences.json
        │  align      match sentences across languages (LaBSE + dynamic programming)
        ▼
work/<id>/align/NN.tsv                   ← one readable table per chapter
        │  review     list the matches most likely to be wrong
        ▼
work/<id>/review.tsv   ──fix──▶ sources/<id>/anchors.yaml, then align again
        │  build      group short sentences into pages
        ▼
public/books/<id>.json + public/books/library.json
```

## One-time setup

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh    # if `uv` isn't installed
uv sync                                            # installs Python deps (CPU-only torch)
```

The first `align` downloads the LaBSE model (~1.8 GB) into `~/.cache/huggingface`.

## 1. Get the texts

Download both books from Project Gutenberg as **Plain Text UTF-8** and save them
as `sources/<id>/fr.txt` and `sources/<id>/en.txt`. Use a short id that sorts
well, like `2-jeunes-filles`. The Gutenberg header and licence are stripped
automatically.

## 2. Write `book.yaml`

Copy `sources/1-swann/book.yaml` and edit it. The only part that takes thought
is the chapter list. Open both texts and find the heading that starts each
chapter; write a regular expression for each one.

```yaml
chapters:
  - fr: { title: "Combray I", start: '^PREMIÈRE PARTIE\s+COMBRAY\s+I\.$' }
    en: { title: "Overture", start: '^OVERTURE$', nth: 2 }
```

- Patterns run in multi-line mode: `^` / `$` match the start/end of a line;
  `\s+` spans line breaks, so a heading on several lines is one pattern.
- Headings are searched in order, each after the previous one. If a heading
  also appears earlier (in a table of contents), `nth: 2` skips the first hit.
- The chapter text starts right after the match and ends at the next
  chapter's heading. Anything before the first chapter (title page,
  dedication) is dropped.
- The two languages must have the same chapters. If the translation merges or
  splits chapters, follow the coarser division in both.

## 3. Split chapters

```sh
uv run python -m pipeline chapters <id>
```

It prints sentence counts per chapter and the length ratio between the two
languages. The ratios should all be close to each other; a chapter flagged
`<-- check the chapter headings` almost always means a heading matched in the
wrong place.

## 4. Align

```sh
uv run python -m pipeline align <id>
```

Embedding takes about 9 minutes per volume on a 16-core CPU (Swann's Way:
~4,500 sentences per language). The embeddings are cached in `work/<id>/cache`,
so re-running after adding anchors takes seconds; only changing the text or the
sentence splitting triggers a new embedding. Each chapter gets
`work/<id>/align/NN.tsv`:

| src | tgt | score | src_text | tgt_text |
|---|---|---|---|---|
| `12` | `13-14` | 0.41 | one French sentence | two English ones |
| `57` | `-@61` | -0.10 | a sentence | nothing — the translation dropped it |

## 5. Review

```sh
uv run python -m pipeline review <id>
```

Writes `work/<id>/review.tsv`: every match scoring below 0.12, worst first.
Low scores are often fine: free translation, very short sentences, or verse the
translator replaced (Swann's Way has 12 flagged matches, all of this kind). A
*run* of low scores in one place means the alignment slipped there.

To fix a slip, add an anchor to `sources/<id>/anchors.yaml`. An anchor says
"a page starts at this sentence in both languages"; the aligner then solves
each side of it separately:

```yaml
- chapter: 3
  fr: "Mais le docteur Cottard"     # any snippet unique within the chapter's sentences
  en: "But Dr. Cottard"
```

Then run `align` and `review` again.

**Reviewing with Claude Code:** ask it to *"read work/<id>/review.tsv and the
matching align/NN.tsv files, find places where the alignment slipped, and add
anchors to sources/<id>/anchors.yaml"*. It can read both languages and judge
whether low scores are real mistakes.

## 6. Build

```sh
uv run python -m pipeline build <id>
```

Groups consecutive matches into pages — short sentences are merged until a page
has about 90 characters (without going past 450), and an unmatched sentence is
attached to its neighbour — then writes `public/books/<id>.json` and refreshes
`public/books/library.json`.

Or do everything at once: `uv run python -m pipeline run <id>` (or `run all`).

## Trying it

```sh
python3 -m http.server -d public 8000
```

Open http://localhost:8000. To try it on a phone on the same network, use the
computer's IP address instead of `localhost`. Installing as an app and offline
reading need HTTPS, so deploy `public/` to any static host for that.

## Notes

- Bookmarks and reading positions are stored by sentence index, not page
  number, so rebuilding a volume keeps them — as long as the *French* sentence
  splitting doesn't change. Avoid editing the French text after people have
  started reading; fixing the alignment is always safe.
- Tunables: `LOW_SCORE`, `PAGE_MIN_CHARS`, `PAGE_MAX_CHARS` in
  `pipeline/steps.py`; `SKIP`, `MAX_SPAN` in `pipeline/align.py`.

"""Book preparation pipeline.

    uv run python -m pipeline <command> <volume-id>   (or "all")

Commands, in order:
    chapters  split both texts into chapters and sentences → work/<id>/sentences.json
    align     align sentences chapter by chapter         → work/<id>/align/NN.tsv
    review    list the alignments most likely to be wrong → work/<id>/review.tsv
    build     group into pages and write the reader file → public/books/<id>.json
    run       chapters + align + review + build

See PREPARING.md for the full guide.
"""

import sys

from . import steps
from .text import PrepError


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in steps.COMMANDS:
        print(__doc__)
        return 2
    command, target = argv
    volumes = steps.all_volumes() if target == "all" else [target]
    try:
        for vol in volumes:
            for step in steps.COMMANDS[command]:
                step(steps.Volume(vol))
        if command in ("build", "run"):
            steps.write_library()
    except PrepError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

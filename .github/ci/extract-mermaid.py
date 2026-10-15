# SPDX-FileCopyrightText: 2026 Mateusz Okulanis
# SPDX-License-Identifier: GPL-3.0-or-later
"""Write every ```mermaid fence in docs/ out as its own file, for the renderer.

A diagram that does not parse renders as "Syntax error in text" on the published
site and nowhere else: mkdocs is happy, the strict build is happy, and the reader
is not. So CI renders them, and the only thing standing in its way is that they
live inside Markdown.
"""

import pathlib
import re
import sys

PATTERN = re.compile(r"```mermaid\n(.*?)```", re.S)


def main() -> int:
    docs = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    count = 0
    for markdown in sorted(docs.rglob("*.md")):
        for index, block in enumerate(PATTERN.findall(markdown.read_text("utf-8")), start=1):
            (out / f"{markdown.stem}-{index}.mmd").write_text(block, "utf-8")
            count += 1
    print(f"{count} diagrams from {docs}")
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())

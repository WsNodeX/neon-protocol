# Contributing to neon-protocol

## Reporting a problem

Open an issue with:

- your OS and Python version
- whether the window renders at all (`--headless --frames 60` tells us if it is
  a rendering bug or a display bug)
- the frame rate you see, if the problem is performance

## Sending a change

1. Fork, branch from `main` (note: this repo uses `master`, check
   `git symbolic-ref --short HEAD` first).
2. Keep gameplay changes separate from rendering changes — they are reviewed
   against different criteria.
3. If you touch the renderer, run the benchmark before and after and paste both
   numbers:

   ```bash
   python -m neon_protocol.main --bench
   ```

## Code style

- No asset files. Every texture, sprite and sound is generated at runtime; a PR
  that adds a `.png` or `.wav` will be asked to generate it instead.
- Tuning numbers live in `config.py`. Do not scatter magic numbers through the
  renderer.
- The fixed 1/120s physics step is load-bearing — AI reaction times and movement
  feel both depend on it.

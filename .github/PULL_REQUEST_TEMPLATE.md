## What changed and why

## Which rule(s) from CONTRIBUTING.md this touches

If you changed `capture.py`, `notes.py`, `store.py` or `files.py`, say which of
append-only / never-calculate / ambiguity-stops-the-machine / Decimal-only
applies here, and how this change keeps it.

## Tests

- [ ] `uv run pytest -q` passes
- [ ] `uv run ruff check .` passes
- [ ] New behaviour has a test that would fail without this change
- [ ] If this touches `install.sh` or `install.ps1`: `bash -n install.sh` (and,
      if you can, an actual run of the installer) still works

# Local gate — the ONLY merge gate. Branch protection needs GitHub Pro on a
# private repo, so CI has never gated a merge here; every merge rests on this.
# Run from the repo root of the tree under test (worktree or primary).
#
# The final leg verifies the FROZEN ARTIFACT in engine/dist/ — a green source
# tree proves nothing about the bundle that ships. Its provenance assertion
# fails by name on a stale or dirty-tree freeze; fix with: make freeze.
#
# PY is the engine venv's python, resolved after `cd engine`. A fresh worktree
# has no venv — point PY at the primary's:
#   make gate PY=/Users/james/code/iofast/engine/.venv/bin/python

SHELL := /bin/bash
PY ?= .venv/bin/python

.PHONY: gate freeze bundle

gate:
	@cd engine && test -x $(PY) || { echo "no python at PY=$(PY) (resolved after cd engine) — pass PY=<abs path to venv python>"; exit 1; }
	git status --porcelain  # informational: shows what the tree carries; NOT a cleanliness assertion
	cd engine && PYTHONPATH=src $(PY) -m pytest tests -q
	# cargo's tauri build script needs the frozen sidecar dir; without it cargo
	# runs ZERO tests and still exits 0 — so require the dir AND a nonzero pass count.
	test -e engine/dist/iofast/iofast
	set -o pipefail; cd app/src-tauri && mkdir -p target && cargo test 2>&1 | tee target/cargo-test.log && grep -Eq 'test result: ok\. [1-9][0-9]* passed' target/cargo-test.log
	cd app && pnpm run build
	cd engine && PYTHONPATH=src $(PY) packaging/verify_frozen.py dist/iofast/iofast

freeze:
	@cd engine && test -x $(PY) || { echo "no python at PY=$(PY) (resolved after cd engine) — pass PY=<abs path to venv python>"; exit 1; }
	@cd engine && test -x $(dir $(PY))pyinstaller || { echo "no pyinstaller beside PY=$(PY) — install the [build] extra in that venv"; exit 1; }
	cd engine && $(dir $(PY))pyinstaller --noconfirm packaging/iofast.spec
	cd engine && PYTHONPATH=src $(PY) packaging/verify_frozen.py dist/iofast/iofast

# The ONLY sanctioned way to produce the .app bundle: a fresh, verified freeze
# first (tauri's beforeBuildCommand is frontend-only by design), then verify
# the sidecar tauri actually COPIED into the .app — the copy is the step that
# failed historically, and engine/dist being fresh does not prove it happened.
bundle: freeze
	cd app && pnpm tauri build
	cd engine && PYTHONPATH=src $(PY) packaging/verify_frozen.py \
		../app/src-tauri/target/release/bundle/macos/iofast.app/Contents/Resources/engine/iofast

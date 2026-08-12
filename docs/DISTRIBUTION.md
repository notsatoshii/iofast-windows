# Distribution

How iofast is packaged today, and what it takes to turn on signing, notarization,
and auto-update later. Everything below is **structure only** — signing and
distribution are deferred by decision (PLAN.md Phase 4). Nothing here is active.

## Current state

- Builds are **unsigned local builds**. `pnpm tauri build` in `app/` produces an
  `.app` and `.dmg` with no Developer ID signature.
- The frozen engine is bundled into the `.app` via `bundle.resources` in
  `tauri.conf.json` (`engine/dist/iofast/` → `Contents/Resources/engine/`), so
  `engine && pyinstaller --noconfirm packaging/iofast.spec` must run before
  `pnpm tauri build`. PyInstaller output is a directory (a binary plus an
  `_internal/` tree of dylibs), which is why this is `resources` and not
  `externalBin` (that expects a single file). The resource target is `engine/`,
  not `iofast/`: Tauri also copies resources next to the cargo binary in
  `target/<profile>/`, and a directory named after the binary breaks the build.
- **Measured signing state** (local, 2026-08-12, debug bundle): every Mach-O in
  the bundled engine tree is already **ad-hoc signed** by PyInstaller on arm64;
  `codesign -s - --deep --force` over the whole `.app` takes <1s;
  `codesign --verify --strict --deep` passes. `spctl --assess --type execute`
  **rejects** the ad-hoc bundle — expected, Gatekeeper acceptance needs the
  Developer ID + notarization flow below. Note for that flow: notarization
  requires a valid Developer ID signature on **each** Mach-O, including the
  ~76 dylibs under `Resources/engine/_internal/` — plan for a sign pass over
  the engine tree, not just the app binary.
- `.github/workflows/release.yml` builds the same unsigned bundle on a `v*` tag (or
  manual dispatch) and uploads it as a CI artifact. It uses **no secrets** and runs
  green without any Apple credentials.
- Distribution to other machines is **not on**. An unsigned build runs locally but
  Gatekeeper will warn any other user who downloads it.

## Turning on signing + notarization

The config placeholders are already in place; enabling is credentials + uncommenting.

**1. What's already wired (in `app/src-tauri/tauri.conf.json`, under `bundle.macOS`):**

| Key | Value | Role |
|---|---|---|
| `hardenedRuntime` | `true` | Codesign runs with hardened runtime (a notarization requirement). |
| `entitlements` | `entitlements.plist` | Points at `app/src-tauri/entitlements.plist`, applied during the sign step. |
| `minimumSystemVersion` | `10.15` | Deployment floor; unrelated to signing but set here. |

`signingIdentity` is deliberately **unset**. With it absent, Tauri does not codesign
unless the `APPLE_SIGNING_IDENTITY` environment variable is present at build — that
absence is what keeps every local build and today's CI unsigned and inert.

**2. The env vars that activate it** (add as repo secrets, then uncomment the `env:`
block in `release.yml`'s `pnpm tauri build` step):

- `APPLE_SIGNING_IDENTITY` — the "Developer ID Application: …" identity. Its presence
  is the switch that makes Tauri sign at all.
- `APPLE_ID` — the Apple developer account email.
- `APPLE_PASSWORD` — an app-specific password for that account.
- `APPLE_TEAM_ID` — the developer team id.

With those four set, `pnpm tauri build` signs with the identity and Tauri runs
notarization itself. No change to the build step other than the env block.

**3. The notarization flow** (Tauri drives this when the env is present; the manual
equivalent, for reference):

```
xcrun notarytool submit iofast_0.1.0_aarch64.dmg \
  --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" \
  --wait
xcrun stapler staple iofast.app
```

`entitlements.plist` grants the four hardened-runtime exceptions the frozen sidecar
and the downloaded Chromium need (JIT, unsigned executable memory, library-validation
disable, dyld env vars) — see the comment block in that file for why each is present.

## Auto-update — why the crate is NOT added yet

Tauri v2 auto-update needs the `tauri-plugin-updater` **crate** plus a signing keypair.
Adding that crate is the same risk class that already forced two features to be
hand-rolled in this codebase:

- `tauri-plugin-clipboard-manager` was rejected because it drags edition2024 crates
  (zune-jpeg / getrandom) into the macOS graph, which **cargo 1.78 refused to parse**
  — see the `clipboard_text` comment in `app/src-tauri/src/main.rs` (lines ~158-164).
- `tauri-plugin-dialog` was rejected for the same reason (it pulls `toml ^1.0` →
  `toml_parser`, which requires edition2024) — see the folder-picker comment in
  `app/src-tauri/src/store.rs` (lines ~194-198).

**The toolchain floor moved (D2, 2026-08-12):** `app/src-tauri/rust-toolchain.toml`
pins **1.97.1**, adopted after a throwaway-worktree experiment built the committed
lockfile `--locked` with all tests green. The edition2024 rejections above are
historical context — those plugins are viable now, and each gets adopted only when
its slice needs it (updater at distribution time, single-instance with the D3
LaunchAgent work, clipboard in slice #7). The committed `Cargo.lock` stays
load-bearing: it was resolved under cargo 1.78 and still builds unchanged.

To add `tauri-plugin-updater` later:

1. Add the `tauri-plugin-updater` crate (verify it resolves under the pinned toolchain
   first).
2. Generate a keypair (`tauri signer generate`); keep the private key as a secret.
3. Set `bundle.createUpdaterArtifacts` and a `plugins.updater` block (`endpoints` +
   `pubkey`) in `tauri.conf.json`.
4. Register the plugin in `src/main.rs`.

An inert `plugins.updater` placeholder is deliberately **not** left in the config:
it would be dead config with no plugin behind it, and adds nothing until step 1 lands.

## Windows / SmartScreen

Deferred with the rest of distribution. Per PLAN.md Phase 4: SmartScreen and Defender
flag an unsigned `.exe` that downloads and runs a fingerprint-spoofing Chromium.
Windows code-signing is part of the same "structure ready, turn on later" bucket as
macOS signing. No structure for it is added in this pass (this workflow is macOS-only).

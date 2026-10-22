# iofast Android companion: plan (v1)

Companion to iofast-windows. Captures the **source URL** of whatever is on
screen when Eric double-presses the side key on his Galaxy Z Fold2 (Android 13 /
One UI 5.1), queues it locally, and hands it to the PC engine for grab + vault
filing. No scraping on the phone, no private APIs, no CAPTCHA tricks.

## Trigger

**Primary: Side-key double press.** One UI 5.1 supports
Settings → Advanced features → Side key → Double press → Open app. User picks
iofast from the app list. Our trampoline activity receives the launch, reads
the last-captured URL from the accessibility service, shows a toast, and exits.
No special permissions beyond what the accessibility service already holds.

**Secondary: Share sheet.** Any app's Share → iofast captures `ACTION_SEND`
text/plain or text/uri-list. Works without the accessibility service and covers
apps whose view trees we cannot read. Always available in v1.

**Not in v1: Assistant route.** VoiceInteractionService + AssistContent can
surface `webUri` from Chrome and some apps on long-press home, but Samsung's
One UI replaces Google Assistant with Bixby by default and does not reliably
forward AssistContent from YouTube/X/Instagram. Documented as a future option;
not worth the integration cost until Eric confirms he uses Bixby-as-assistant
and the target apps opt in.

## URL extraction, ranked by reliability

Each extractor runs only when its package is the last foreground app. The
accessibility service records `(package, url?, viewTreeText?)` on every window
change; the trampoline reads the most recent entry.

| Rank | Method | Apps | Notes |
|---|---|---|---|
| 1 | AssistContent.webUri | Chrome, Samsung Internet | Best signal when available; null for most non-browser apps |
| 2 | Accessibility node tree: URL bar | Chrome (`com.android.chrome:id/url_bar`), Samsung Internet (`com.sec.android.app.sbrowser:id/url_bar`) | Reliable; read-only via a11y API |
| 3 | Accessibility node tree: deep link / share text | YouTube (`youtube.com/watch`, `youtu.be`), X (`x.com`, `twitter.com`), Instagram (`instagram.com/p`, `/reel`) | Pattern-match against visible text nodes; works because these apps render the canonical URL or a recognizable slug in the view tree even when the address bar is hidden |
| 4 | Automate Share → Copy link | Any app with a share action | Last resort before screenshot; fragile (timing, UI changes); logged but not auto-triggered in v1 — user falls back to manual share-sheet capture |
| 5 | Screenshot + OCR | Anything | True last resort; out of scope for v1 (no ML Kit dependency); documented as v2 escape hatch |

Extractors are stateless functions: `(packageName, viewTreeSnapshot) -> String?`.
Unit tests run against recorded JSON fixtures of real view trees from each
target app (captured once via `adb shell uiautomator dump` and sanitized).

## Where the link goes

### v1 (ships now)

- **Local queue.** Room database on device. Each entry: url, source app,
  timestamp, status (pending/sent/failed). Queue screen lists entries with
  copy / share / delete actions. Toast "Captured: <url>" on capture.
- **Share-sheet target.** iofast appears in Android's share sheet. Tapping it
  adds the shared URL to the same queue. This is the zero-setup path: works
  today, no network, no accounts.

### Transport options (decision deferred to Eric)

The transport interface is `LinkTransport.send(url: String): Result<Unit>` with
a `StubTransport` that always succeeds and logs. Three candidates, none chosen:

1. **LAN / Tailscale HTTP POST.** Phone posts to `http://<pc>:PORT/api/capture`
   on the same network. PC runs a tiny listener that calls `iofast grab <url>`
   and pipes the result into `iofast vault file`. Lowest latency, no cloud,
   requires both devices on the same LAN or Tailscale. **Recommended** if Eric
   already uses Tailscale between phone and PC.
2. **Cloud relay (e.g., Cloudflare Worker, Supabase).** Phone posts to a
   hosted endpoint; PC polls or receives a webhook. Works across networks but
   introduces an account, a secret, and a third party. Only if LAN is not
   viable.
3. **ADB reverse / USB tether.** `adb reverse tcp:PORT tcp:PORT` while plugged
   in. Zero-config but requires a cable. Good for desk use; not for pocket.

Eric picks after v1 ships and he has tried the queue + share sheet. The
interface is ready to swap implementations without touching extractors or UI.

## Out of scope for v1

- Screenshot + OCR capture path
- Assistant / Bixby integration
- Auto-send to PC (stub only)
- Settings screen beyond enable/disable accessibility service
- Multi-account or multi-device routing
- Wear OS, tablets beyond Fold2 form factor

## Build constraints

- Kotlin, Gradle (Kotlin DSL), minSdk 30, targetSdk 34
- No Compose yet (XML layouts for v1 simplicity; migrate later)
- Dependencies: Room, Material3, AndroidX Core KTX, Lifecycle
- Tests: JUnit 5 + Robolectric for extractor unit tests against fixtures
- CI: `./gradlew assembleDebug testDebugUnitTest` must pass
- No phone required to build or test
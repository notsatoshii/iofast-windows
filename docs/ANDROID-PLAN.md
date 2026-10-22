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

### Social apps (YouTube, X, Instagram): Share→Copy-link flow (rank 1)

These apps do NOT render their canonical URL as visible text in the view tree.
The accessibility service drives the app's own Share → Copy link action:

1. Trampoline signals the accessibility service with the target package.
2. Service finds the Share button by resource id or content description
   (Korean: 공유, 링크 복사; English: Share, Copy link) and clicks it.
3. Service finds and clicks "Copy link" in the share sheet.
4. Trampoline polls the clipboard (bounded: 3 s total, 200 ms intervals).
5. ClipboardCleaner validates the URL against the app's pattern, strips
   tracking params (si=, igsh=, utm_*, s=/t= on X), normalizes to canonical
   form, and restores the previous clipboard content.
6. If the share sheet is still open after timeout, service sends
   GLOBAL_ACTION_BACK to dismiss it. Never loops.

View-tree text matching remains as a fallback for these apps but is expected
to return null on real devices.

### Browsers (Chrome, Samsung Internet): URL bar route (rank 1)

Browsers render the full URL in the address bar node. The accessibility
service reads it directly from the view tree via regex matching.

### Fallback: manual share sheet

If neither automated path succeeds, the toast says "Couldn't find the link;
use Share → iofast" directing Eric to use the share-sheet target manually.

### Last resort: screenshot + OCR (out of scope for v1)

True escape hatch for apps we cannot automate. Not implemented in v1.

## Accessibility service design

- Tracks per-package snapshots in a map; only stores relevant packages
  (Chrome, Samsung Internet, YouTube, X, Instagram).
- Ignores our own package, com.android.systemui, launchers, and keyboards
  so the trampoline always reads the app that was in front just before the
  side-key press.
- Snapshots stale after 10 seconds; trampoline rejects older ones.
- Config: canRetrieveWindowContent, flagReportViewIds,
  flagRetrieveInteractiveWindows, canPerformGestures.
- Disabled service: trampoline opens accessibility settings with explanation.

## Fold2 specifics

- One UI 5.1 / Android 13; side key double press → Open app.
- Trampoline is exported, translucent theme, finishes immediately — no UI
  flash on either cover screen or main screen.
- Accessibility service works on both displays (same window events).

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

## Capturing real view-tree fixtures (for Eric)

Current test fixtures are synthetic. Replace them with real dumps from the
Fold2 once it is connected over USB with developer options + USB debugging on:

```bash
# YouTube: open a video, then:
adb shell uiautomator dump /sdcard/yt.xml && adb pull /sdcard/yt.xml android/app/src/test/resources/fixtures/youtube_viewtree_real.xml

# X: open a post, then:
adb shell uiautomator dump /sdcard/x.xml && adb pull /sdcard/x.xml android/app/src/test/resources/fixtures/x_viewtree_real.xml

# Instagram: open a post or reel, then:
adb shell uiautomator dump /sdcard/ig.xml && adb pull /sdcard/ig.xml android/app/src/test/resources/fixtures/instagram_viewtree_real.xml

# Chrome: open any page, then:
adb shell uiautomator dump /sdcard/chrome.xml && adb pull /sdcard/chrome.xml android/app/src/test/resources/fixtures/chrome_viewtree_real.xml

# Samsung Internet: open any page, then:
adb shell uiautomator dump /sdcard/sbrowser.xml && adb pull /sdcard/sbrowser.xml android/app/src/test/resources/fixtures/samsung_internet_viewtree_real.xml
```

After pulling, sanitize (remove personal data) and update ExtractorTest to
load the `_real.xml` fixtures alongside the synthetic ones.

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
- Tests: JUnit 4 + Robolectric for extractor unit tests against fixtures
- CI: `./gradlew assembleDebug testDebugUnitTest` must pass
- No phone required to build or test
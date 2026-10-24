package com.iofast.android

/**
 * Same capture as the side-key launcher, opened by an accessibility
 * shortcut (One UI: Side and Volume up keys). It is an activity target, so
 * pressing the shortcut starts it; it never turns the accessibility service
 * on or off. iofast's own windows are ignored by the service, so the
 * snapshot is still of the app that was in front.
 *
 * Fallback only: the system starts it with plain FLAG_ACTIVITY_NEW_TASK,
 * so the app in front is "user leaving" and YouTube goes picture-in-picture
 * before this onCreate runs. The preferred target is the service itself
 * (see UrlCaptureAccessibilityService.buttonCallback), which opens no window.
 */
class AccessibilityShortcutCaptureActivity : CaptureTrampolineActivity() {
    override val trigger = "accessibility shortcut (activity)"
}

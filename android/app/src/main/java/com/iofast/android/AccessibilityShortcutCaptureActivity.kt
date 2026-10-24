package com.iofast.android

/**
 * Same capture as the side-key launcher, opened by an accessibility
 * shortcut (One UI: Side and Volume up keys). It is an activity target, so
 * pressing the shortcut starts it; it never turns the accessibility service
 * on or off. iofast's own windows are ignored by the service, so the
 * snapshot is still of the app that was in front.
 */
class AccessibilityShortcutCaptureActivity : CaptureTrampolineActivity() {
    override val trigger = "accessibility shortcut"
}

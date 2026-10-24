package com.iofast.android

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.xmlpull.v1.XmlPullParser

/**
 * The system finds accessibility shortcut targets by querying activities
 * with CATEGORY_ACCESSIBILITY_SHORTCUT_TARGET and reading their
 * android.accessibilityshortcut.target meta-data. Check both are wired.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AccessibilityShortcutTargetTest {

    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val component = ComponentName(context, AccessibilityShortcutCaptureActivity::class.java)

    @Test
    fun `shortcut activity is exported with the target meta-data`() {
        val info = context.packageManager.getActivityInfo(component, PackageManager.GET_META_DATA)
        assertTrue(info.exported)
        val res = info.metaData.getInt("android.accessibilityshortcut.target")
        assertNotEquals(0, res)

        val parser = context.resources.getXml(res)
        var event = parser.next()
        while (event != XmlPullParser.START_TAG) event = parser.next()
        assertEquals("accessibility-shortcut-target", parser.name)
        val ns = "http://schemas.android.com/apk/res/android"
        assertNotNull(parser.getAttributeValue(ns, "summary"))
        assertNotNull(parser.getAttributeValue(ns, "description"))
    }

    @Test
    fun `shortcut activity answers the shortcut-target category`() {
        val intent = Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_ACCESSIBILITY_SHORTCUT_TARGET)
            .setPackage(context.packageName)
        val found = context.packageManager.queryIntentActivities(intent, PackageManager.GET_META_DATA)
            .map { it.activityInfo.name }
        assertEquals(listOf(AccessibilityShortcutCaptureActivity::class.java.name), found)
    }

    @Test
    fun `shortcut activity runs the same capture as the side key`() {
        assertTrue(CaptureTrampolineActivity::class.java.isAssignableFrom(AccessibilityShortcutCaptureActivity::class.java))
    }
}

package com.iofast.android

import android.accessibilityservice.AccessibilityServiceInfo
import android.content.ComponentName
import android.content.Context
import android.content.pm.PackageManager
import androidx.test.core.app.ApplicationProvider
import com.iofast.android.CaptureRouter.Route
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class CaptureRouterTest {

    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test
    fun `routes each app to its capture`() {
        assertEquals(Route.NO_SNAPSHOT, CaptureRouter.routeFor(null))
        assertEquals(Route.SHARE_FLOW, CaptureRouter.routeFor("com.google.android.youtube"))
        assertEquals(Route.SHARE_FLOW, CaptureRouter.routeFor("com.x.android"))
        assertEquals(Route.SHARE_FLOW, CaptureRouter.routeFor("com.instagram.android"))
        assertEquals(Route.BROWSER, CaptureRouter.routeFor("com.android.chrome"))
        assertEquals(Route.BROWSER, CaptureRouter.routeFor("com.sec.android.app.sbrowser"))
        assertEquals(Route.UNSUPPORTED, CaptureRouter.routeFor("com.example.other"))
    }

    /**
     * Without this flag a shortcut press on the service toggles it off
     * (targetSdk > Q); with it, the press reaches the button callback.
     */
    @Test
    fun `service config requests the accessibility button`() {
        val info = context.packageManager.getServiceInfo(
            ComponentName(context, UrlCaptureAccessibilityService::class.java),
            PackageManager.GET_META_DATA
        )
        val res = info.metaData.getInt("android.accessibilityservice")
        val parser = context.resources.getXml(res)
        var flags: String? = null
        while (parser.next() != org.xmlpull.v1.XmlPullParser.END_DOCUMENT) {
            if (parser.eventType == org.xmlpull.v1.XmlPullParser.START_TAG && parser.name == "accessibility-service") {
                flags = (0 until parser.attributeCount)
                    .firstOrNull { parser.getAttributeName(it) == "accessibilityFlags" }
                    ?.let { parser.getAttributeValue(it) }
            }
        }
        val value = flags?.toIntOrNull() ?: flags?.removePrefix("0x")?.toLongOrNull(16)?.toInt()
        assertTrue("flags=$flags", value != null &&
            (value and AccessibilityServiceInfo.FLAG_REQUEST_ACCESSIBILITY_BUTTON) != 0)
    }

    @Test
    fun `app targets above Q so the shortcut does not toggle the service`() {
        assertTrue(context.applicationInfo.targetSdkVersion > 29)
    }
}

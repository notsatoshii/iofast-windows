package com.iofast.android.diag

import android.content.Intent
import androidx.test.core.app.ApplicationProvider
import com.iofast.android.CaptureTrampolineActivity
import com.iofast.android.MainActivity
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ProblemLogTest {

    private val ctx get() = ApplicationProvider.getApplicationContext<android.app.Application>()

    @Before
    fun setUp() {
        ProblemLog.init(ctx)
        ProblemLog.clear()
    }

    @Test
    fun `report holds steps and crashes`() {
        ProblemLog.note("capture", "hello step")
        ProblemLog.recordCrash(Thread.currentThread(), IllegalStateException("boom"))
        val r = ProblemLog.report(ctx)
        assertTrue(r.contains("hello step"))
        assertTrue(r.contains("boom"))
        assertTrue(r.contains("accessibility on: false"))
    }

    @Test
    fun `clear empties the log`() {
        ProblemLog.note("x", "gone soon")
        ProblemLog.clear()
        assertFalse(ProblemLog.report(ctx).contains("gone soon"))
    }

    @Test
    fun `home screen opens and shows capture off`() {
        val activity = Robolectric.buildActivity(MainActivity::class.java).setup().get()
        val status = activity.findViewById<android.widget.TextView>(com.iofast.android.R.id.statusText)
        assertEquals(activity.getString(com.iofast.android.R.string.status_off), status.text.toString())
    }

    @Test
    fun `side key with capture off opens the home screen`() {
        val activity = Robolectric.buildActivity(CaptureTrampolineActivity::class.java).setup().get()
        val next: Intent? = shadowOf(activity).nextStartedActivity
        assertEquals(MainActivity::class.java.name, next?.component?.className)
        assertTrue(activity.isFinishing)
        assertTrue(ProblemLog.report(ctx).contains("accessibility is off"))
    }
}

package com.iofast.android.update

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class UpdateWorkerTest {

    @Test
    fun onlyAHigherVersionCodeIsAnUpdate() {
        assertTrue(UpdateWorker.isNewer(9, 8))
        assertFalse(UpdateWorker.isNewer(8, 8))
        assertFalse(UpdateWorker.isNewer(7, 8))
    }

    @Test
    fun sha256MatchesKnownValue() {
        val f = File.createTempFile("iofast", ".bin")
        f.writeText("abc")
        assertEquals("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", UpdateWorker.sha256Hex(f))
        f.delete()
    }
}

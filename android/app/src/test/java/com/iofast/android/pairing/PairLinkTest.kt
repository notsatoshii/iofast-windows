package com.iofast.android.pairing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PairLinkTest {

    private val key = "Ab3_dEf-9.xyz~0123456789"

    @Test
    fun `reads the key from a pairing link`() {
        assertEquals(key, PairLink.parse("iofast://pair?key=$key"))
    }

    @Test
    fun `ignores other parameters and case of scheme and host`() {
        assertEquals(key, PairLink.parse("IOFAST://Pair?v=1&key=$key&x=y"))
    }

    @Test
    fun `decodes percent-encoding`() {
        assertEquals(key, PairLink.parse("iofast://pair?key=Ab3_dEf-9.xyz%7E0123456789"))
    }

    @Test
    fun `rejects other schemes hosts and missing keys`() {
        assertNull(PairLink.parse(null))
        assertNull(PairLink.parse(""))
        assertNull(PairLink.parse("https://pair?key=$key"))
        assertNull(PairLink.parse("iofast://other?key=$key"))
        assertNull(PairLink.parse("iofast://pair"))
        assertNull(PairLink.parse("iofast://pair?key="))
        assertNull(PairLink.parse("iofast://pair?token=$key"))
        assertNull(PairLink.parse("not a uri at all %%"))
    }

    @Test
    fun `rejects keys that are too short or carry odd characters`() {
        assertNull(PairLink.parse("iofast://pair?key=short"))
        assertNull(PairLink.parse("iofast://pair?key=has%20space%20in%20the%20key"))
        assertNull(PairLink.parse("iofast://pair?key=${"a".repeat(257)}"))
    }

    @Test
    fun `pasted text can be a bare key or the whole link`() {
        assertEquals(key, PairLink.fromPasted("  $key\n"))
        assertEquals(key, PairLink.fromPasted("iofast://pair?key=$key"))
        assertNull(PairLink.fromPasted("hello"))
        assertNull(PairLink.fromPasted(""))
        assertNull(PairLink.fromPasted(null))
    }
}

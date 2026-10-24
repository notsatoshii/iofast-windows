package com.iofast.android.transport

import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.ServerSocket

/** Runs HttpTransport against a throwaway local HTTP server. */
class HttpTransportTest {

    private lateinit var server: FakeHttpServer

    private val testKey = "test-key-not-real"
    private val link = OutgoingLink("https://example.com/a?b=\"c\"", "com.android.chrome", 1_700_000_000_123L)

    @Before
    fun start() {
        server = FakeHttpServer().apply { status = 201; reply = """{"id":"abc123"}""" }
    }

    @After
    fun stop() = server.close()

    private fun base() = "http://127.0.0.1:${server.port}/iofast-inbox/"
    private fun transport() = HttpTransport(base(), testKey, 2_000, 2_000)

    @Test
    fun `201 is sent and request matches the contract`() = runBlocking {
        val r = transport().send(link)
        assertEquals(SendResult.Sent("abc123", false), r)
        val s = server.requests.single()
        assertEquals("POST", s.method)
        assertEquals("/iofast-inbox/links", s.path)
        assertEquals("Bearer $testKey", s.header("Authorization"))
        assertTrue(s.header("Content-Type")!!.startsWith("application/json"))
        assertEquals(
            """{"url":"https://example.com/a?b=\"c\"","source":"com.android.chrome","captured_at":"2023-11-14T22:13:20.123Z"}""",
            s.body
        )
    }

    @Test
    fun `200 duplicate is sent`() = runBlocking {
        server.status = 200; server.reply = """{"id": "x9", "duplicate": true}"""
        assertEquals(SendResult.Sent("x9", true), transport().send(link))
    }

    @Test
    fun `401 is unauthorized`() = runBlocking {
        server.status = 401; server.reply = """{"error":"bad key"}"""
        assertEquals(SendResult.Unauthorized, transport().send(link))
    }

    @Test
    fun `5xx and 429 are retryable`() = runBlocking {
        server.status = 503; server.reply = ""
        assertTrue(transport().send(link) is SendResult.Retryable)
        server.status = 429
        assertTrue(transport().send(link) is SendResult.Retryable)
    }

    @Test
    fun `400 is rejected and the error never holds the key`() = runBlocking {
        server.status = 400; server.reply = """{"error":"url required"}"""
        val r = transport().send(link)
        assertTrue(r is SendResult.Rejected)
        assertFalse((r as SendResult.Rejected).error.contains(testKey))
    }

    @Test
    fun `server down is retryable`() = runBlocking {
        val port = ServerSocket(0).use { it.localPort }
        val r = HttpTransport("http://127.0.0.1:$port", testKey, 1_000, 1_000).send(link)
        assertTrue(r is SendResult.Retryable)
        assertFalse((r as SendResult.Retryable).error.contains(testKey))
    }

    @Test
    fun `health ok sends no key, not ok fails`() = runBlocking {
        server.status = 200; server.reply = """{"ok": true}"""
        assertTrue(transport().health().isSuccess)
        val s = server.requests.last()
        assertEquals("GET", s.method)
        assertEquals("/iofast-inbox/health", s.path)
        assertNull(s.header("Authorization"))
        server.status = 502; server.reply = ""
        assertTrue(transport().health().isFailure)
    }

    @Test
    fun `json helpers escape and read`() {
        assertEquals(""""a\nb\u0001\\"""", HttpTransport.jsonString("a\nb\u0001\\"))
        assertEquals("""q\"r""", HttpTransport.jsonField("""{"id":"q\"r"}""", "id"))
        assertNull(HttpTransport.jsonField("""{"other":"1"}""", "id"))
    }

    @Test
    fun `status reads the PC verdict and title`() = runBlocking {
        server.status = 200
        server.reply = """{"id":"abc","done":true,"ok":true,"filed":true,"title":"Agents: \"How\" — 안녕"}"""
        val st = transport().status("abc")!!
        assertTrue(st.done && st.ok && st.filed)
        assertEquals("Agents: \"How\" — 안녕", st.title)
        val r = server.requests.single()
        assertEquals("GET", r.method)
        assertEquals("/iofast-inbox/links/abc", r.path)
        assertEquals("Bearer $testKey", r.header("Authorization"))
    }

    @Test
    fun `status is null when the server cannot say`() = runBlocking {
        server.status = 404
        server.reply = """{"error":"not found"}"""
        assertNull(transport().status("abc"))
    }
}

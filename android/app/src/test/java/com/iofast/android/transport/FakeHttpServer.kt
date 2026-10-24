package com.iofast.android.transport

import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketException
import kotlin.concurrent.thread

/**
 * Minimal one-request-per-connection HTTP/1.1 server for transport tests.
 * android.jar hides com.sun.net.httpserver from unit tests and MockWebServer
 * is not a dependency, so this reads the request line, headers and a
 * Content-Length body, records them, and answers with [status] / [reply].
 */
class FakeHttpServer : AutoCloseable {

    data class Request(val method: String, val path: String, val headers: Map<String, String>, val body: String) {
        fun header(name: String): String? = headers[name.lowercase()]
    }

    @Volatile var status = 200
    @Volatile var reply = ""
    val requests: MutableList<Request> = java.util.Collections.synchronizedList(mutableListOf())

    private val socket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    val port: Int get() = socket.localPort

    init {
        thread(isDaemon = true, name = "fake-http") {
            while (!socket.isClosed) {
                val client = try { socket.accept() } catch (_: SocketException) { break }
                client.use { c ->
                    val input = BufferedInputStream(c.getInputStream())
                    val requestLine = readLine(input) ?: return@use
                    val headers = mutableMapOf<String, String>()
                    while (true) {
                        val line = readLine(input) ?: break
                        if (line.isEmpty()) break
                        val i = line.indexOf(':')
                        if (i > 0) headers[line.substring(0, i).trim().lowercase()] = line.substring(i + 1).trim()
                    }
                    val length = headers["content-length"]?.toIntOrNull() ?: 0
                    val body = ByteArray(length)
                    var read = 0
                    while (read < length) {
                        val n = input.read(body, read, length - read)
                        if (n < 0) break
                        read += n
                    }
                    val parts = requestLine.split(' ')
                    requests += Request(parts[0], parts.getOrElse(1) { "" }, headers, String(body, 0, read, Charsets.UTF_8))

                    val bytes = reply.toByteArray(Charsets.UTF_8)
                    val head = "HTTP/1.1 $status X\r\nContent-Type: application/json\r\nContent-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
                    val out = c.getOutputStream()
                    out.write(head.toByteArray(Charsets.US_ASCII))
                    out.write(bytes)
                    out.flush()
                }
            }
        }
    }

    private fun readLine(input: InputStream): String? {
        val buf = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b < 0) return if (buf.size() == 0) null else buf.toString("US-ASCII")
            if (b == '\n'.code) return buf.toString("US-ASCII").trimEnd('\r')
            buf.write(b)
        }
    }

    override fun close() = socket.close()
}

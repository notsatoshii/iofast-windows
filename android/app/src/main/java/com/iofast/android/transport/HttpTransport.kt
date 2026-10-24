package com.iofast.android.transport

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant

/**
 * Talks to the PC inbox on the droplet:
 *   POST {base}/links  Authorization: Bearer <key>  {"url","source","captured_at"}
 *   GET  {base}/health
 *   GET  {base}/links/{id}  -> {"done","ok","filed","title"} (how the PC handled it)
 * Plain HttpURLConnection so the app needs no HTTP library.
 * Error strings never include the key or the Authorization header.
 */
class HttpTransport(
    baseUrl: String,
    private val key: String,
    private val connectTimeoutMs: Int = 10_000,
    private val readTimeoutMs: Int = 15_000
) : LinkTransport {

    private val base = baseUrl.trimEnd('/')

    override suspend fun send(link: OutgoingLink): SendResult = withContext(Dispatchers.IO) {
        val body = buildString {
            append("{\"url\":").append(jsonString(link.url))
            append(",\"source\":").append(jsonString(link.source))
            append(",\"captured_at\":").append(jsonString(isoUtc(link.capturedAtMillis)))
            append('}')
        }
        var conn: HttpURLConnection? = null
        try {
            conn = open("$base/links", "POST")
            conn.setRequestProperty("Authorization", "Bearer $key")
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.doOutput = true
            val bytes = body.toByteArray(Charsets.UTF_8)
            conn.setFixedLengthStreamingMode(bytes.size)
            conn.outputStream.use { it.write(bytes) }

            val code = conn.responseCode
            val reply = readBody(conn, code)
            when {
                code == 201 || code == 200 -> SendResult.Sent(
                    remoteId = jsonField(reply, "id"),
                    duplicate = DUPLICATE_TRUE.containsMatchIn(reply)
                )
                code == 401 -> SendResult.Unauthorized
                code == 408 || code == 429 || code >= 500 -> SendResult.Retryable("HTTP $code")
                else -> SendResult.Rejected("HTTP $code ${reply.take(200)}".trim())
            }
        } catch (e: IOException) {
            SendResult.Retryable(e.javaClass.simpleName + (e.message?.let { ": $it" } ?: ""))
        } finally {
            conn?.disconnect()
        }
    }

    override suspend fun health(): Result<Unit> = withContext(Dispatchers.IO) {
        var conn: HttpURLConnection? = null
        try {
            conn = open("$base/health", "GET")
            val code = conn.responseCode
            val reply = readBody(conn, code)
            if (code == 200 && OK_TRUE.containsMatchIn(reply)) Result.success(Unit)
            else Result.failure(IOException("health answered HTTP $code"))
        } catch (e: IOException) {
            Result.failure(e)
        } finally {
            conn?.disconnect()
        }
    }

    /** How the PC handled a sent link; null when the server could not say. */
    suspend fun status(remoteId: String): LinkStatus? = withContext(Dispatchers.IO) {
        var conn: HttpURLConnection? = null
        try {
            conn = open("$base/links/" + java.net.URLEncoder.encode(remoteId, "UTF-8"), "GET")
            conn.setRequestProperty("Authorization", "Bearer $key")
            val code = conn.responseCode
            val reply = readBody(conn, code)
            if (code != 200) null
            else LinkStatus(
                done = jsonBool(reply, "done"),
                ok = jsonBool(reply, "ok"),
                filed = jsonBool(reply, "filed"),
                title = jsonField(reply, "title")?.let(::unescape).orEmpty()
            )
        } catch (_: IOException) {
            null
        } finally {
            conn?.disconnect()
        }
    }

    private fun open(url: String, method: String): HttpURLConnection =
        (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = connectTimeoutMs
            readTimeout = readTimeoutMs
            instanceFollowRedirects = false
            setRequestProperty("Accept", "application/json")
        }

    private fun readBody(conn: HttpURLConnection, code: Int): String = try {
        val stream = if (code >= 400) conn.errorStream else conn.inputStream
        stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText().take(4096) } ?: ""
    } catch (_: IOException) {
        ""
    }

    companion object {
        private val DUPLICATE_TRUE = Regex(""""duplicate"\s*:\s*true""")
        private val OK_TRUE = Regex(""""ok"\s*:\s*true""")
        private const val BS = '\\'

        fun isoUtc(millis: Long): String = Instant.ofEpochMilli(millis).toString()

        /** JSON string literal with the escapes RFC 8259 requires. */
        fun jsonString(s: String): String = buildString {
            append('"')
            for (c in s) when {
                c == '"' -> append(BS).append('"')
                c == BS -> append(BS).append(BS)
                c == '\n' -> append(BS).append('n')
                c == '\r' -> append(BS).append('r')
                c == '\t' -> append(BS).append('t')
                c < ' ' -> append(BS).append(String.format("u%04x", c.code))
                else -> append(c)
            }
            append('"')
        }

        fun jsonBool(json: String, name: String): Boolean =
            Regex("\"" + Regex.escape(name) + "\"" + """\s*:\s*true""").containsMatchIn(json)

        /** Undoes the escapes JSON.stringify writes in a title. */
        fun unescape(s: String): String = Regex("""\\(u[0-9a-fA-F]{4}|.)""").replace(s) { m ->
            val e = m.groupValues[1]
            when {
                e.length == 5 -> e.substring(1).toInt(16).toChar().toString()
                e == "n" || e == "t" || e == "r" -> " "
                else -> e
            }
        }

        /** Reads a top-level string field from a small flat JSON reply (escapes left as sent). */
        fun jsonField(json: String, name: String): String? =
            Regex("\"" + Regex.escape(name) + "\"" + """\s*:\s*"((?:[^"\\]|\\.)*)"""" )
                .find(json)?.groupValues?.get(1)
    }
}

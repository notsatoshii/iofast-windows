package com.iofast.android.transport

/** One captured link as the PC inbox receives it. */
data class OutgoingLink(
    val url: String,
    /** Package name of the app the link came from ("unknown" when not known). */
    val source: String,
    val capturedAtMillis: Long
)

/** What happened to one send attempt. Never carries the phone key. */
sealed class SendResult {
    /** Server stored it (201) or already had it from the last 10 minutes (200 + duplicate). */
    data class Sent(val remoteId: String?, val duplicate: Boolean) : SendResult()

    /** 401: the phone key is wrong or was revoked. Retrying will not help. */
    object Unauthorized : SendResult()

    /** Network down, timeout, 5xx, 408, 429: try again later. */
    data class Retryable(val error: String) : SendResult()

    /** Any other 4xx or a malformed reply: retrying the same request will not help. */
    data class Rejected(val error: String) : SendResult()
}

/** Sends captured links to the PC inbox. */
interface LinkTransport {
    suspend fun send(link: OutgoingLink): SendResult

    /** True when the inbox answers GET /health with ok. */
    suspend fun health(): Result<Unit>
}

/** Accepts everything; for tests and previews only. */
class StubTransport : LinkTransport {
    override suspend fun send(link: OutgoingLink): SendResult = SendResult.Sent(null, false)
    override suspend fun health(): Result<Unit> = Result.success(Unit)
}

package com.iofast.android.transport

/**
 * Transport interface for sending captured links to the PC engine.
 * Implementations handle LAN/Tailscale HTTP POST, cloud relay, or ADB.
 * v1 ships with StubTransport (local queue only).
 */
interface LinkTransport {
 /** Send a URL to the PC. Returns Result.success on success, Result.failure on error. */
 suspend fun send(url: String): Result<Unit>
}

/**
 * Stub implementation: always succeeds, logs the URL.
 * Used in v1 while Eric decides on the transport mechanism.
 */
class StubTransport : LinkTransport {
 override suspend fun send(url: String): Result<Unit> {
 android.util.Log.d("iofast.Transport", "Stub: would send $url")
 return Result.success(Unit)
 }
}
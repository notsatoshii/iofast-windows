package com.iofast.android.pairing

import android.content.Context
import android.content.SharedPreferences

/**
 * Phone key, inbox address and the last send outcome, in the app's
 * private SharedPreferences (not readable by other apps; excluded from
 * backup by the manifest's backup rules). The key is never logged.
 */
class PairingStore(context: Context) {

    companion object {
        const val PREFS = "iofast_pairing"
        const val DEFAULT_BASE_URL = "https://agentos.rest/iofast-inbox"

        private const val K_KEY = "phone_key"
        private const val K_BASE = "base_url"
        private const val K_LAST_AT = "last_send_at"
        private const val K_LAST_OK = "last_send_ok"
        private const val K_LAST_DETAIL = "last_send_detail"
    }

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    val key: String? get() = prefs.getString(K_KEY, null)?.takeIf { it.isNotBlank() }
    val baseUrl: String get() = prefs.getString(K_BASE, null) ?: DEFAULT_BASE_URL
    val isPaired: Boolean get() = key != null

    fun save(key: String) {
        prefs.edit()
            .putString(K_KEY, key)
            .putString(K_BASE, DEFAULT_BASE_URL)
            .remove(K_LAST_AT).remove(K_LAST_OK).remove(K_LAST_DETAIL)
            .apply()
    }

    fun clear() {
        prefs.edit().clear().apply()
    }

    data class LastSend(val at: Long, val ok: Boolean, val detail: String?)

    val lastSend: LastSend?
        get() {
            val at = prefs.getLong(K_LAST_AT, 0L)
            if (at == 0L) return null
            return LastSend(at, prefs.getBoolean(K_LAST_OK, false), prefs.getString(K_LAST_DETAIL, null))
        }

    fun recordSend(ok: Boolean, detail: String?) {
        prefs.edit()
            .putLong(K_LAST_AT, System.currentTimeMillis())
            .putBoolean(K_LAST_OK, ok)
            .putString(K_LAST_DETAIL, detail)
            .apply()
    }
}

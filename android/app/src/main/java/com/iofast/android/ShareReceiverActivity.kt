package com.iofast.android

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.iofast.android.diag.ProblemLog
import com.iofast.android.send.Captures

/**
 * Receives ACTION_SEND text/plain or text/uri-list from any app's share sheet.
 * Extracts the first URL found in the shared text, queues it, notifies, exits.
 */
class ShareReceiverActivity : AppCompatActivity() {

    companion object {
        private val URL = Regex("""https?://[^\s"'<>]+""", RegexOption.IGNORE_CASE)

        /** Apps often share "Look at this https://..." rather than a bare link. */
        fun firstUrl(text: String?): String? = text?.let { URL.find(it)?.value }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val url = firstUrl(intent.getStringExtra(Intent.EXTRA_TEXT))
        ProblemLog.note("share-in", "from ${callingPackage ?: "?"}: ${url ?: "no link in shared text"}")

        if (url != null) {
            Captures.record(this, url, callingPackage)
        } else {
            Toast.makeText(this, R.string.toast_no_url, Toast.LENGTH_SHORT).show()
        }
        finish()
    }
}

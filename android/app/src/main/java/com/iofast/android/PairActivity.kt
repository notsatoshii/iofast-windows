package com.iofast.android

import android.content.Intent
import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import com.iofast.android.databinding.ActivityPairBinding
import com.iofast.android.diag.ProblemLog
import com.iofast.android.pairing.PairLink
import com.iofast.android.pairing.PairingStore
import com.iofast.android.send.SendWorker
import kotlinx.coroutines.launch

/**
 * Opened by the PC's pairing link iofast://pair?key=<phone key>.
 * Saves the key, checks GET /health, then sends anything that was waiting.
 */
class PairActivity : AppCompatActivity() {
    private lateinit var binding: ActivityPairBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityPairBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.btnOpenApp.setOnClickListener {
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
            finish()
        }
        handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    private fun handle(intent: Intent?) {
        val key = PairLink.parse(intent?.dataString)
        if (key == null) {
            ProblemLog.note("pair", "pairing link rejected (not a valid iofast://pair link)")
            binding.pairTitle.setText(R.string.pair_title)
            binding.pairDetail.setText(R.string.pair_invalid)
            return
        }
        binding.pairTitle.setText(R.string.pair_connected)
        binding.pairDetail.setText(R.string.pair_checking)
        connect(this, key) { ok ->
            binding.pairDetail.setText(if (ok) R.string.pair_health_ok else R.string.pair_health_failed)
            binding.pairDetail.setTextColor(ContextCompat.getColor(this, if (ok) R.color.status_on else R.color.status_off))
        }
    }

    companion object {
        /**
         * Shared by the pairing link and the home screen's paste field:
         * store the key, check the inbox answers, queue waiting links.
         */
        fun connect(activity: AppCompatActivity, key: String, onHealth: (Boolean) -> Unit) {
            val app = activity.applicationContext
            val store = PairingStore(app)
            store.save(key)
            ProblemLog.note("pair", "phone key saved")
            activity.lifecycleScope.launch {
                val ok = com.iofast.android.transport.HttpTransport(store.baseUrl, key).health().isSuccess
                ProblemLog.note("pair", "inbox health " + if (ok) "ok" else "did not answer")
                onHealth(ok)
            }
            IofastApp.appScope.launch {
                try {
                    SendWorker.enqueueAllPending(app, retryFailed = true)
                } catch (t: Throwable) {
                    ProblemLog.note("send", "could not queue waiting links: $t")
                }
            }
        }
    }
}

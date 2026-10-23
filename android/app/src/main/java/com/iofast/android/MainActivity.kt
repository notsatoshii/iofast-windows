package com.iofast.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.iofast.android.data.AppDatabase
import com.iofast.android.databinding.ActivityMainBinding
import com.iofast.android.diag.ProblemLog
import kotlinx.coroutines.launch

/**
 * Home screen (the "iofast" launcher entry): is URL capture on, how to set
 * up the side key, the captured links, and the problem log to send back
 * when something goes wrong.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.btnAccessibility.setOnClickListener {
            startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
        }
        binding.btnLinks.setOnClickListener {
            startActivity(Intent(this, QueueActivity::class.java))
        }
        binding.btnCopyLog.setOnClickListener {
            val cm = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            cm.setPrimaryClip(ClipData.newPlainText("iofast problem log", ProblemLog.report(this)))
            Toast.makeText(this, R.string.toast_log_copied, Toast.LENGTH_SHORT).show()
        }
        binding.btnShareLog.setOnClickListener {
            startActivity(Intent.createChooser(
                Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_SUBJECT, "iofast problem log")
                    putExtra(Intent.EXTRA_TEXT, ProblemLog.report(this@MainActivity))
                },
                getString(R.string.action_share)
            ))
        }
        binding.btnClearLog.setOnClickListener {
            ProblemLog.clear()
            refresh()
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                AppDatabase.get(this@MainActivity).capturedLinkDao().observeAll().collect { links ->
                    binding.btnLinks.text = getString(R.string.btn_links, links.size)
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    private fun refresh() {
        val on = ProblemLog.isServiceEnabled(this)
        binding.statusText.setText(if (on) R.string.status_on else R.string.status_off)
        binding.statusText.setTextColor(
            ContextCompat.getColor(this, if (on) R.color.status_on else R.color.status_off)
        )
        binding.btnAccessibility.setText(if (on) R.string.btn_accessibility_on else R.string.btn_accessibility)
        binding.logText.text = ProblemLog.report(this)
    }
}

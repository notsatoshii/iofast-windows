package com.iofast.android

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.text.format.DateUtils
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.iofast.android.data.AppDatabase
import com.iofast.android.databinding.ActivityMainBinding
import com.iofast.android.diag.ProblemLog
import com.iofast.android.data.SendState
import com.iofast.android.pairing.PairLink
import com.iofast.android.pairing.PairingStore
import com.iofast.android.send.CaptureNotifier
import kotlinx.coroutines.launch

/**
 * Home screen (the "iofast" launcher entry): is URL capture on, how to set
 * up the side key, the captured links, and the problem log to send back
 * when something goes wrong.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var pairing: PairingStore

    private val askNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
            ProblemLog.note("notify", "permission " + if (granted) "granted" else "denied")
            refresh()
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        pairing = PairingStore(this)

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
        binding.btnPair.setOnClickListener {
            if (pairing.isPaired) {
                pairing.clear()
                ProblemLog.note("pair", "disconnected from PC")
                Toast.makeText(this, R.string.pc_disconnected, Toast.LENGTH_SHORT).show()
                refresh()
                return@setOnClickListener
            }
            val key = PairLink.fromPasted(binding.keyInput.text?.toString())
            if (key == null) {
                binding.keyInputLayout.error = getString(R.string.pc_key_invalid)
                return@setOnClickListener
            }
            binding.keyInputLayout.error = null
            binding.keyInput.setText("")
            PairActivity.connect(this, key) { ok ->
                Toast.makeText(
                    this,
                    if (ok) R.string.pair_health_ok else R.string.pair_health_failed,
                    Toast.LENGTH_LONG
                ).show()
                refresh()
            }
            refresh()
        }
        binding.btnNotif.setOnClickListener {
            if (Build.VERSION.SDK_INT >= 33 && !notificationPermissionGranted() &&
                shouldShowRequestPermissionRationale(Manifest.permission.POST_NOTIFICATIONS)
            ) {
                askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
            } else {
                startActivity(
                    Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                )
            }
        }
        binding.btnClearLog.setOnClickListener {
            ProblemLog.clear()
            refresh()
        }

        // Android 13+: ask once on first open; after that the status line + button cover it.
        if (savedInstanceState == null && Build.VERSION.SDK_INT >= 33 && !notificationPermissionGranted()) {
            askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                AppDatabase.get(this@MainActivity).capturedLinkDao().observeAll().collect { links ->
                    binding.btnLinks.text = getString(R.string.btn_links, links.size)
                    val waiting = links.count { it.status == SendState.PENDING }
                    binding.pcPendingText.visibility = if (waiting > 0) View.VISIBLE else View.GONE
                    binding.pcPendingText.text = getString(R.string.pc_pending, waiting)
                    refreshPairing()
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

        val canNotify = CaptureNotifier.canNotify(this)
        binding.notifText.visibility = if (canNotify) View.GONE else View.VISIBLE
        binding.btnNotif.visibility = if (canNotify) View.GONE else View.VISIBLE
        refreshPairing()
    }

    private fun refreshPairing() {
        val paired = pairing.isPaired
        binding.pcStatusText.setText(if (paired) R.string.pc_paired else R.string.pc_not_paired)
        binding.pcStatusText.setTextColor(
            ContextCompat.getColor(this, if (paired) R.color.status_on else R.color.status_off)
        )
        val last = pairing.lastSend
        binding.pcLastText.visibility = if (paired) View.VISIBLE else View.GONE
        binding.pcLastText.text = when {
            last == null -> getString(R.string.pc_last_none)
            last.ok -> getString(R.string.pc_last_ok, ago(last.at))
            else -> getString(R.string.pc_last_failed, ago(last.at), last.detail ?: "?")
        }
        binding.pcHelpText.visibility = if (paired) View.GONE else View.VISIBLE
        binding.keyInputLayout.visibility = if (paired) View.GONE else View.VISIBLE
        binding.btnPair.setText(if (paired) R.string.pc_disconnect else R.string.pc_connect)
    }

    private fun ago(at: Long): CharSequence =
        DateUtils.getRelativeTimeSpanString(at, System.currentTimeMillis(), DateUtils.MINUTE_IN_MILLIS)

    private fun notificationPermissionGranted(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
}

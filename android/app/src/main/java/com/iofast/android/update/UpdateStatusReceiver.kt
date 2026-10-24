package com.iofast.android.update

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.iofast.android.R
import com.iofast.android.diag.ProblemLog
import com.iofast.android.send.CaptureNotifier

/**
 * Result of an update install. When Android wants the user to confirm
 * (first self-update, or "install unknown apps" not yet allowed), posts
 * one notification whose tap opens the system's Update prompt.
 */
class UpdateStatusReceiver : BroadcastReceiver() {

    companion object {
        private const val ID_UPDATE = 2
    }

    override fun onReceive(context: Context, intent: Intent) {
        val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
        when (status) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                @Suppress("DEPRECATION")
                val confirm = intent.getParcelableExtra<Intent>(Intent.EXTRA_INTENT) ?: return
                confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                ProblemLog.note("update", "waiting for the user to tap Update")
                if (!CaptureNotifier.canNotify(context)) return
                CaptureNotifier.ensureChannel(context)
                val tap = PendingIntent.getActivity(
                    context, ID_UPDATE, confirm,
                    PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
                )
                val n = NotificationCompat.Builder(context, CaptureNotifier.CHANNEL_ID)
                    .setSmallIcon(R.drawable.ic_stat_link)
                    .setContentTitle(context.getString(R.string.notif_update_ready))
                    .setContentText(context.getString(R.string.notif_update_tap))
                    .setAutoCancel(true)
                    .setContentIntent(tap)
                    .build()
                try {
                    NotificationManagerCompat.from(context).notify(ID_UPDATE, n)
                } catch (e: SecurityException) {
                    ProblemLog.note("notify", "not allowed: ${e.javaClass.simpleName}")
                }
            }
            PackageInstaller.STATUS_SUCCESS -> ProblemLog.note("update", "installed")
            else -> ProblemLog.note(
                "update",
                "install failed ($status): ${intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)}"
            )
        }
    }
}

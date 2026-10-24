package com.iofast.android.send

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.iofast.android.MainActivity
import com.iofast.android.R
import com.iofast.android.diag.ProblemLog

/**
 * One notification per capture on the "Captures" channel: posted as
 * "Captured: <url>", then updated in place with where the link went.
 * When notifications are off, the short messages fall back to toasts.
 */
object CaptureNotifier {
    const val CHANNEL_ID = "captures"
    private const val ID_COULD_NOT_READ = 1
    private const val ID_BASE = 1000

    fun ensureChannel(context: Context) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        // High so it pops over the app like the old toast did; silent so it never buzzes.
        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.channel_captures),
            NotificationManager.IMPORTANCE_HIGH
        ).apply {
            description = context.getString(R.string.channel_captures_desc)
            setSound(null, null)
            enableVibration(false)
        }
        nm.createNotificationChannel(channel)
    }

    fun canNotify(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return false
        val nmc = NotificationManagerCompat.from(context)
        if (!nmc.areNotificationsEnabled()) return false
        val channel = context.getSystemService(NotificationManager::class.java)?.getNotificationChannel(CHANNEL_ID)
        return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
    }

    fun captured(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_captured, url), null)

    fun sent(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_sent), url)

    fun savedOffline(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_saved_offline), url)

    fun notPaired(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_not_paired), url)

    fun keyRejected(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_key_rejected), url)

    fun failed(context: Context, linkId: Long, url: String) =
        post(context, linkId, context.getString(R.string.notif_send_failed), url)

    /** The share flow could not get a link out of the app on screen. */
    fun couldNotRead(context: Context) {
        if (!canNotify(context)) {
            toast(context, context.getString(R.string.toast_fallback_share))
            return
        }
        notify(
            context, ID_COULD_NOT_READ,
            builder(context)
                .setContentTitle(context.getString(R.string.notif_could_not_read))
                .setContentText(context.getString(R.string.notif_use_share))
                .setStyle(NotificationCompat.BigTextStyle().bigText(context.getString(R.string.notif_use_share)))
                .setTimeoutAfter(60_000)
        )
    }

    /** Toast fallback for the capture itself when notifications are off. */
    fun toastIfSilent(context: Context, message: String) {
        if (!canNotify(context)) toast(context, message)
    }

    private fun post(context: Context, linkId: Long, title: String, text: String?) {
        if (!canNotify(context)) return
        val b = builder(context).setContentTitle(title)
        if (text != null) b.setContentText(text)
        notify(context, ID_BASE + (linkId % 1_000_000).toInt(), b)
    }

    private fun builder(context: Context): NotificationCompat.Builder {
        ensureChannel(context)
        val open = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )
        return NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_link)
            .setCategory(NotificationCompat.CATEGORY_STATUS)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setOnlyAlertOnce(true)
            .setAutoCancel(true)
            .setContentIntent(open)
    }

    private fun notify(context: Context, id: Int, b: NotificationCompat.Builder) {
        try {
            NotificationManagerCompat.from(context).notify(id, b.build())
        } catch (e: SecurityException) {
            ProblemLog.note("notify", "not allowed: ${e.javaClass.simpleName}")
        }
    }

    private fun toast(context: Context, message: String) {
        Handler(Looper.getMainLooper()).post {
            Toast.makeText(context.applicationContext, message, Toast.LENGTH_LONG).show()
        }
    }
}

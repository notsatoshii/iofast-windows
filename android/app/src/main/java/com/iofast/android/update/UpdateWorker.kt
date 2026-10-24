package com.iofast.android.update

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.iofast.android.diag.ProblemLog
import com.iofast.android.pairing.PairingStore
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/**
 * Self-update from the inbox server ({base}/app/phone.json + phone.apk,
 * phone key). Checks on app start and every 6 hours; a newer versionCode
 * is downloaded, checked against its sha256 and handed to PackageInstaller.
 *
 * Android 12+ installs it without a prompt once iofast is the installer of
 * record, i.e. from the second self-update on; until then (or if the
 * system insists) [UpdateStatusReceiver] posts a one-tap notification.
 * Android itself rejects an APK signed with a different key.
 */
class UpdateWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        private const val PERIODIC = "iofast-update"
        private const val NOW = "iofast-update-now"

        fun schedule(context: Context) {
            val net = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            val wm = WorkManager.getInstance(context)
            wm.enqueueUniquePeriodicWork(
                PERIODIC, ExistingPeriodicWorkPolicy.KEEP,
                PeriodicWorkRequestBuilder<UpdateWorker>(6, TimeUnit.HOURS).setConstraints(net).build()
            )
            wm.enqueueUniqueWork(
                NOW, ExistingWorkPolicy.KEEP,
                OneTimeWorkRequestBuilder<UpdateWorker>().setConstraints(net).build()
            )
        }

        fun isNewer(remote: Long, installed: Long) = remote > installed

        fun sha256Hex(file: File): String {
            val md = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buf = ByteArray(64 * 1024)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    md.update(buf, 0, n)
                }
            }
            return md.digest().joinToString("") { "%02x".format(it) }
        }
    }

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val store = PairingStore(ctx)
        val key = store.key ?: return Result.success()
        val base = store.baseUrl.trimEnd('/')
        return try {
            val release = JSONObject(get("$base/app/phone.json", key).bufferedReader().use { it.readText() })
            val remote = release.getLong("versionCode")
            val installed = ctx.packageManager.getPackageInfo(ctx.packageName, 0).longVersionCode
            if (!isNewer(remote, installed)) return Result.success()

            val apk = File(ctx.cacheDir, "update.apk")
            get("$base/app/phone.apk", key).use { input -> apk.outputStream().use { input.copyTo(it) } }
            val want = release.getString("sha256").lowercase()
            if (sha256Hex(apk) != want) {
                ProblemLog.note("update", "download of ${release.optString("versionName")} failed its checksum")
                apk.delete()
                return Result.retry()
            }
            ProblemLog.note("update", "installing ${release.optString("versionName")} (code $remote)")
            install(ctx, apk)
            Result.success()
        } catch (t: Throwable) {
            ProblemLog.note("update", "check failed: ${t.javaClass.simpleName}")
            Result.retry()
        }
    }

    private fun get(url: String, key: String) =
        (URL(url).openConnection() as HttpURLConnection).run {
            connectTimeout = 15_000
            readTimeout = 60_000
            setRequestProperty("Authorization", "Bearer $key")
            if (responseCode != 200) throw IllegalStateException("HTTP $responseCode")
            inputStream
        }

    private fun install(ctx: Context, apk: File) {
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("iofast.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val status = PendingIntent.getBroadcast(
                ctx, id,
                Intent(ctx, UpdateStatusReceiver::class.java),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
            )
            session.commit(status.intentSender)
        }
    }
}

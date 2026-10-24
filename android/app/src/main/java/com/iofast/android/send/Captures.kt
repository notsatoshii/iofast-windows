package com.iofast.android.send

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.iofast.android.IofastApp
import com.iofast.android.R
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.CapturedLink
import com.iofast.android.diag.ProblemLog
import com.iofast.android.pairing.PairingStore
import kotlinx.coroutines.launch

/**
 * The one place a captured link is saved: queue row, notification, send job.
 * Side key (browser), clipboard read (YouTube / X / Instagram) and the share
 * sheet all call [record].
 */
object Captures {

    fun record(context: Context, url: String, sourcePackage: String?) {
        val app = context.applicationContext
        // Toast only when there will be no notification to say it.
        CaptureNotifier.toastIfSilent(app, app.getString(R.string.toast_captured, url))
        IofastApp.appScope.launch {
            try {
                val dao = AppDatabase.get(app).capturedLinkDao()
                val id = dao.insert(CapturedLink(url = url, sourcePackage = sourcePackage))
                CaptureNotifier.captured(app, id, url)
                if (!PairingStore(app).isPaired) {
                    dao.markWaiting(id, SendWorker.NOT_PAIRED)
                    CaptureNotifier.notPaired(app, id, url)
                    ProblemLog.note("send", "#$id saved; phone not paired")
                    return@launch
                }
                if (!isOnline(app)) {
                    CaptureNotifier.savedOffline(app, id, url)
                    ProblemLog.note("send", "#$id saved; offline, will send later")
                }
                SendWorker.enqueue(app, id)
            } catch (t: Throwable) {
                ProblemLog.note("capture", "save failed: $t")
            }
        }
    }

    fun isOnline(context: Context): Boolean = try {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val caps = cm?.getNetworkCapabilities(cm.activeNetwork)
        caps?.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) == true
    } catch (_: Throwable) {
        true
    }
}

package com.iofast.android.send

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.iofast.android.diag.ProblemLog
import com.iofast.android.pairing.PairingStore
import com.iofast.android.transport.HttpTransport
import java.util.concurrent.TimeUnit

/**
 * After a link reaches the inbox, asks the server every minute or so how
 * the PC handled it, and updates the capture's notification to
 * "Saved to vault: <title>" (or why it was not). Gives up quietly after
 * about two hours, e.g. when the PC is off.
 */
class VaultStatusWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        const val KEY_LINK_ID = "link_id"
        const val KEY_REMOTE_ID = "remote_id"
        const val KEY_URL = "url"
        const val MAX_TRIES = 120

        fun enqueue(context: Context, linkId: Long, remoteId: String, url: String) {
            val request = OneTimeWorkRequestBuilder<VaultStatusWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setInitialDelay(45, TimeUnit.SECONDS)
                .setBackoffCriteria(BackoffPolicy.LINEAR, 60, TimeUnit.SECONDS)
                .setInputData(workDataOf(KEY_LINK_ID to linkId, KEY_REMOTE_ID to remoteId, KEY_URL to url))
                .addTag("iofast-vault-status")
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("vault-$linkId", ExistingWorkPolicy.KEEP, request)
        }
    }

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val id = inputData.getLong(KEY_LINK_ID, -1L)
        val remoteId = inputData.getString(KEY_REMOTE_ID) ?: return Result.success()
        val url = inputData.getString(KEY_URL).orEmpty()
        val store = PairingStore(ctx)
        val key = store.key ?: return Result.success()

        val status = HttpTransport(store.baseUrl, key).status(remoteId)
        if (status == null || !status.done) {
            if (runAttemptCount + 1 >= MAX_TRIES) {
                ProblemLog.note("vault", "#$id no answer from the PC, stopped asking")
                return Result.success()
            }
            return Result.retry()
        }
        val name = status.title.ifBlank { url }
        when {
            status.ok && status.filed -> CaptureNotifier.savedToVault(ctx, id, name)
            status.ok -> CaptureNotifier.notFiled(ctx, id, name)
            else -> CaptureNotifier.pcFailed(ctx, id, name)
        }
        ProblemLog.note("vault", "#$id done ok=${status.ok} filed=${status.filed}")
        return Result.success()
    }
}

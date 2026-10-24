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
import com.iofast.android.data.AppDatabase
import com.iofast.android.data.SendState
import com.iofast.android.diag.ProblemLog
import com.iofast.android.pairing.PairingStore
import com.iofast.android.transport.HttpTransport
import com.iofast.android.transport.OutgoingLink
import com.iofast.android.transport.SendResult
import java.util.concurrent.TimeUnit

/**
 * Sends one captured link to the PC inbox. Waits for a network, retries
 * with exponential backoff, and updates the capture's notification.
 */
class SendWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    companion object {
        const val KEY_LINK_ID = "link_id"
        /** About a day of backoff (30 s doubling, capped by WorkManager at 5 h). */
        const val MAX_TRIES = 10
        const val NOT_PAIRED = "not paired"

        fun enqueue(context: Context, linkId: Long) {
            val request = OneTimeWorkRequestBuilder<SendWorker>()
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
                .setInputData(workDataOf(KEY_LINK_ID to linkId))
                .addTag("iofast-send")
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork("send-$linkId", ExistingWorkPolicy.KEEP, request)
        }

        /** After pairing or on app start: queue every link still waiting. */
        suspend fun enqueueAllPending(context: Context, retryFailed: Boolean = false) {
            val dao = AppDatabase.get(context).capturedLinkDao()
            if (retryFailed) dao.resetFailed()
            val pending = dao.pending()
            pending.forEach { enqueue(context, it.id) }
            if (pending.isNotEmpty()) ProblemLog.note("send", "queued ${pending.size} waiting link(s)")
        }
    }

    override suspend fun doWork(): Result {
        val ctx = applicationContext
        val id = inputData.getLong(KEY_LINK_ID, -1L)
        val dao = AppDatabase.get(ctx).capturedLinkDao()
        val link = dao.byId(id) ?: return Result.success()
        if (link.status == SendState.SENT) return Result.success()

        val store = PairingStore(ctx)
        val key = store.key
        if (key == null) {
            dao.markWaiting(id, NOT_PAIRED)
            CaptureNotifier.notPaired(ctx, id, link.url)
            ProblemLog.note("send", "#$id waiting: phone not paired")
            return Result.success()
        }

        val outgoing = OutgoingLink(link.url, link.sourcePackage ?: "unknown", link.capturedAt)
        return when (val r = HttpTransport(store.baseUrl, key).send(outgoing)) {
            is SendResult.Sent -> {
                dao.markSent(id, System.currentTimeMillis(), r.remoteId)
                store.recordSend(true, if (r.duplicate) "already in inbox" else null)
                CaptureNotifier.sent(ctx, id, link.url)
                ProblemLog.note("send", "#$id sent" + if (r.duplicate) " (inbox already had it)" else "")
                Result.success()
            }
            SendResult.Unauthorized -> {
                val why = "PC rejected the phone key (401)"
                dao.markAttempt(id, SendState.FAILED, why)
                store.recordSend(false, why)
                CaptureNotifier.keyRejected(ctx, id, link.url)
                ProblemLog.note("send", "#$id failed: $why")
                Result.success()
            }
            is SendResult.Rejected -> {
                dao.markAttempt(id, SendState.FAILED, r.error)
                store.recordSend(false, r.error)
                CaptureNotifier.failed(ctx, id, link.url)
                ProblemLog.note("send", "#$id rejected: ${r.error}")
                Result.success()
            }
            is SendResult.Retryable -> {
                if (runAttemptCount + 1 >= MAX_TRIES) {
                    dao.markAttempt(id, SendState.FAILED, r.error)
                    store.recordSend(false, r.error)
                    CaptureNotifier.failed(ctx, id, link.url)
                    ProblemLog.note("send", "#$id gave up after ${runAttemptCount + 1} tries: ${r.error}")
                    Result.failure()
                } else {
                    dao.markAttempt(id, SendState.PENDING, r.error)
                    store.recordSend(false, r.error)
                    CaptureNotifier.savedOffline(ctx, id, link.url)
                    ProblemLog.note("send", "#$id try ${runAttemptCount + 1} failed, will retry: ${r.error}")
                    Result.retry()
                }
            }
        }
    }
}

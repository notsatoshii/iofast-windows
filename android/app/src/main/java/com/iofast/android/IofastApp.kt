package com.iofast.android

import android.app.Application
import com.iofast.android.diag.ProblemLog
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class IofastApp : Application() {

    companion object {
        /**
         * Outlives any screen. Short-lived activities (trampoline, share
         * receiver) finish right away, so their lifecycleScope would cancel
         * the database insert before it ran.
         */
        val appScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }

    override fun onCreate() {
        super.onCreate()
        ProblemLog.init(this)
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, error ->
            ProblemLog.recordCrash(thread, error)
            previous?.uncaughtException(thread, error)
        }
        ProblemLog.note("app", "process started")
    }
}

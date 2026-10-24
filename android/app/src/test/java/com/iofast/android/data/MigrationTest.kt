package com.iofast.android.data

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Builds a version-1 database exactly as 0.1.x left it (Room's own CREATE
 * statement for the v1 entity), then opens it with the v2 Room database.
 * Room validates the migrated schema against the entity on open, so a
 * mismatch fails here instead of on Eric's phone.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class MigrationTest {

    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val name = "migration-test.db"

    @Before
    fun clean() {
        context.deleteDatabase(name)
    }

    @After
    fun tearDown() {
        context.deleteDatabase(name)
    }

    @Test
    fun `v1 rows survive and become pending sends`() = runBlocking {
        val path = context.getDatabasePath(name).apply { parentFile?.mkdirs() }
        SQLiteDatabase.openOrCreateDatabase(path, null).use { db ->
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS `captured_links` (`id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL, " +
                    "`url` TEXT NOT NULL, `sourcePackage` TEXT, `capturedAt` INTEGER NOT NULL, `status` TEXT NOT NULL)"
            )
            db.execSQL(
                "INSERT INTO captured_links (url, sourcePackage, capturedAt, status) VALUES " +
                    "('https://youtu.be/abc', 'com.google.android.youtube', 1000, 'pending'), " +
                    "('https://example.com', NULL, 2000, 'pending')"
            )
            db.version = 1
        }

        val room = Room.databaseBuilder(context, AppDatabase::class.java, name)
            .addMigrations(AppDatabase.MIGRATION_1_2)
            .allowMainThreadQueries()
            .build()
        try {
            val dao = room.capturedLinkDao()
            val pending = dao.pending()
            assertEquals(listOf("https://youtu.be/abc", "https://example.com"), pending.map { it.url })
            val first = pending.first()
            assertEquals(SendState.PENDING, first.status)
            assertEquals(0, first.attempts)
            assertNull(first.lastError)
            assertNull(first.sentAt)
            assertNull(first.remoteId)
            assertEquals("com.google.android.youtube", first.sourcePackage)

            dao.markSent(first.id, 5000, "r1")
            val sent = dao.byId(first.id)!!
            assertEquals(SendState.SENT, sent.status)
            assertEquals(1, sent.attempts)
            assertEquals("r1", sent.remoteId)
            assertEquals(1, dao.pending().size)

            // New rows written by v2 code use the defaults.
            val id = dao.insert(CapturedLink(url = "https://x.com/a/status/1", sourcePackage = "com.twitter.android"))
            assertEquals(SendState.PENDING, dao.byId(id)!!.status)
        } finally {
            room.close()
        }
    }
}

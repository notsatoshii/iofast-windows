package com.iofast.android.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

@Database(entities = [CapturedLink::class], version = 2, exportSchema = false)
abstract class AppDatabase : RoomDatabase() {
    abstract fun capturedLinkDao(): CapturedLinkDao

    companion object {
        const val NAME = "iofast.db"

        /**
         * v2 adds send tracking. Links saved by 0.1.x keep status 'pending',
         * so they are sent once the phone is paired.
         */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE captured_links ADD COLUMN lastError TEXT")
                db.execSQL("ALTER TABLE captured_links ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0")
                db.execSQL("ALTER TABLE captured_links ADD COLUMN sentAt INTEGER")
                db.execSQL("ALTER TABLE captured_links ADD COLUMN remoteId TEXT")
                db.execSQL("UPDATE captured_links SET status = 'pending' WHERE status NOT IN ('pending','sent','failed')")
            }
        }

        @Volatile private var INSTANCE: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            INSTANCE ?: synchronized(this) {
                INSTANCE ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    NAME
                ).addMigrations(MIGRATION_1_2).build().also { INSTANCE = it }
            }
    }
}

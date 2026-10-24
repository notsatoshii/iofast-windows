package com.iofast.android.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface CapturedLinkDao {
    @Query("SELECT * FROM captured_links ORDER BY capturedAt DESC")
    fun observeAll(): Flow<List<CapturedLink>>

    @Query("SELECT * FROM captured_links WHERE id = :id")
    suspend fun byId(id: Long): CapturedLink?

    /** Links still to send, oldest first. */
    @Query("SELECT * FROM captured_links WHERE status = 'pending' ORDER BY capturedAt ASC")
    suspend fun pending(): List<CapturedLink>

    @Insert
    suspend fun insert(link: CapturedLink): Long

    @Delete
    suspend fun delete(link: CapturedLink)

    @Update
    suspend fun update(link: CapturedLink)

    @Query("UPDATE captured_links SET status = 'sent', sentAt = :at, remoteId = :remoteId, lastError = NULL, attempts = attempts + 1 WHERE id = :id")
    suspend fun markSent(id: Long, at: Long, remoteId: String?)

    @Query("UPDATE captured_links SET status = :status, lastError = :error, attempts = attempts + 1 WHERE id = :id")
    suspend fun markAttempt(id: Long, status: String, error: String?)

    /** Not paired yet: keep it pending, record why, do not count an attempt. */
    @Query("UPDATE captured_links SET status = 'pending', lastError = :error WHERE id = :id")
    suspend fun markWaiting(id: Long, error: String)

    /** After pairing again: give failed links (e.g. rejected key) another go. */
    @Query("UPDATE captured_links SET status = 'pending', attempts = 0 WHERE status = 'failed'")
    suspend fun resetFailed(): Int
}

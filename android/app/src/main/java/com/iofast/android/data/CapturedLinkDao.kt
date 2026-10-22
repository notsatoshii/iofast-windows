package com.iofast.android.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface CapturedLinkDao {
    @Query("SELECT * FROM captured_links ORDER BY capturedAt DESC")
    fun observeAll(): Flow<List<CapturedLink>>

    @Insert
    suspend fun insert(link: CapturedLink): Long

    @Delete
    suspend fun delete(link: CapturedLink)

    @Update
    suspend fun update(link: CapturedLink)
}
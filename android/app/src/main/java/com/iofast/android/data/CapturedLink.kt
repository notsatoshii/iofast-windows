package com.iofast.android.data

import androidx.room.ColumnInfo
import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "captured_links")
data class CapturedLink(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val url: String,
    val sourcePackage: String?,
    val capturedAt: Long = System.currentTimeMillis(),
    /** One of [SendState]: pending, sent, failed. */
    val status: String = SendState.PENDING,
    /** Why the last send did not go through; null once sent. Never holds the key. */
    val lastError: String? = null,
    @ColumnInfo(defaultValue = "0") val attempts: Int = 0,
    val sentAt: Long? = null,
    /** The id the PC inbox gave this link. */
    val remoteId: String? = null
)

object SendState {
    const val PENDING = "pending"
    const val SENT = "sent"
    const val FAILED = "failed"
}

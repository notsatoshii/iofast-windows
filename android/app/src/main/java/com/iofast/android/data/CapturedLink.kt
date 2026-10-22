package com.iofast.android.data

import androidx.room.Entity
import androidx.room.PrimaryKey

@Entity(tableName = "captured_links")
data class CapturedLink(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val url: String,
    val sourcePackage: String?,
    val capturedAt: Long = System.currentTimeMillis(),
    val status: String = "pending"
)
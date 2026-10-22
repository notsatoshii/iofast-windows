package com.iofast.android.shareflow

/**
 * Per-app resource ids and text labels for Share and Copy-link controls.
 * UNVERIFIED until real uiautomator dumps from Eric's Fold2 replace them.
 * Korean + English labels since the phone may be in either locale.
 */
object AppShareLabels {

    data class ShareTarget(
        val shareIds: List<String> = emptyList(),
        val shareTexts: List<String> = emptyList(),
        val shareContentDescriptions: List<String> = emptyList(),
        val copyIds: List<String> = emptyList(),
        val copyTexts: List<String> = emptyList()
    )

    val YOUTUBE = ShareTarget(
        shareIds = listOf(
            "com.google.android.youtube:id/share_button",
            "com.google.android.youtube:id/menu_item_share"
        ),
        shareTexts = listOf("Share", "공유"),
        shareContentDescriptions = listOf("Share", "공유", "Share video", "동영상 공유"),
        copyIds = listOf(),
        copyTexts = listOf("Copy link", "링크 복사", "Copy", "복사")
    )

    val X = ShareTarget(
        shareIds = listOf(
            "com.twitter.android:id/share_button",
            "com.twitter.android:id/tweet_share"
        ),
        shareTexts = listOf("Share", "공유"),
        shareContentDescriptions = listOf("Share", "공유", "Share post", "게시물 공유하기"),
        copyIds = listOf(),
        copyTexts = listOf("Copy link", "링크 복사", "Copy", "복사")
    )

    val INSTAGRAM = ShareTarget(
        shareIds = listOf(
            "com.instagram.android:id/share_button",
            "com.instagram.android:id/row_feed_button_share"
        ),
        shareTexts = listOf("Share", "공유"),
        shareContentDescriptions = listOf("Share", "공유", "Share to", "공유 대상"),
        copyIds = listOf(),
        copyTexts = listOf("Copy link", "링크 복사", "Copy", "복사")
    )

    fun forPackage(packageName: String): ShareTarget? = when (packageName) {
        "com.google.android.youtube" -> YOUTUBE
        "com.twitter.android", "com.x.android" -> X
        "com.instagram.android" -> INSTAGRAM
        else -> null
    }
}
package com.iofast.android.transport

/** The PC's verdict on one sent link, as the inbox reports it. */
data class LinkStatus(val done: Boolean, val ok: Boolean, val filed: Boolean, val title: String)

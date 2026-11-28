package com.example.voicenote.watch.util

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object Formats {
    fun duration(ms: Long): String {
        val totalSeconds = (ms / 1000).coerceAtLeast(0)
        val hours = totalSeconds / 3600
        val minutes = (totalSeconds % 3600) / 60
        val seconds = totalSeconds % 60
        return if (hours > 0) {
            String.format(Locale.US, "%d:%02d:%02d", hours, minutes, seconds)
        } else {
            String.format(Locale.US, "%02d:%02d", minutes, seconds)
        }
    }

    fun fileTimestamp(timestamp: Long): String =
        SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date(timestamp))
}

object Recordings {
    fun dir(context: android.content.Context): File =
        File(context.filesDir, "recordings").apply { mkdirs() }
}

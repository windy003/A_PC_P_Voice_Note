package com.example.r2recorder.util

import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

object Formats {
    private val FILE_TIME_REGEX = Regex("rec-(\\d{8})-(\\d{6})")

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

    fun size(bytes: Long): String {
        if (bytes < 1024) return "$bytes B"
        val kb = bytes / 1024.0
        if (kb < 1024) return String.format(Locale.US, "%.1f KB", kb)
        return String.format(Locale.US, "%.1f MB", kb / 1024.0)
    }

    fun dateTime(timestamp: Long): String =
        SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(Date(timestamp))

    fun displayName(timestamp: Long): String =
        "录音 " + SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.CHINA).format(Date(timestamp))

    fun fileTimestamp(timestamp: Long): String =
        SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(Date(timestamp))

    /** 从录音文件名（rec-YYYYMMDD-HHMMSS）解析时间，无法解析时返回 null。 */
    fun filenameTimeMs(name: String): Long? {
        val match = FILE_TIME_REGEX.find(name) ?: return null
        return try {
            SimpleDateFormat("yyyyMMddHHmmss", Locale.US)
                .apply { isLenient = false }
                .parse(match.groupValues[1] + match.groupValues[2])
                ?.time
        } catch (_: Exception) {
            null
        }
    }
}

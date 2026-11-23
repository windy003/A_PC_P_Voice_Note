package com.example.r2recorder.util

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import com.example.r2recorder.R
import com.example.r2recorder.data.RecordingRepository

object Toasts {
    private const val TOTAL_SUFFIX_MARK = " · 共 "

    fun show(context: Context, message: String, long: Boolean = false) {
        val appContext = context.applicationContext
        val text = withTotalCount(appContext, message)
        Handler(Looper.getMainLooper()).post {
            Toast.makeText(
                appContext,
                text,
                if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT
            ).show()
        }
    }

    /** 在提示末尾追加当前录音总数；已经带有总数的不重复追加。 */
    fun withTotalCount(context: Context, message: String): String {
        if (message.contains(TOTAL_SUFFIX_MARK) && message.trimEnd().endsWith("条")) {
            return message
        }
        val appContext = context.applicationContext
        val total = try {
            RecordingRepository.get(appContext).snapshot().size
        } catch (_: Exception) {
            return message
        }
        return appContext.getString(R.string.toast_total, message, total)
    }
}

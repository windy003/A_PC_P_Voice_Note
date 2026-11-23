package com.example.r2recorder.util

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.widget.Toast

object Toasts {
    fun show(context: Context, message: String, long: Boolean = false) {
        val appContext = context.applicationContext
        Handler(Looper.getMainLooper()).post {
            Toast.makeText(
                appContext,
                message,
                if (long) Toast.LENGTH_LONG else Toast.LENGTH_SHORT
            ).show()
        }
    }
}

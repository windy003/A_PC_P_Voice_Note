package com.example.r2recorder.sync

import android.content.Context

/**
 * 本机已删除、但云端还没删掉的 object key。
 * 先落盘再删云端，避免进程中途退出导致云端副本在下次同步时被重新下载回来。
 */
object PendingDeletes {
    private const val PREFS_NAME = "r2_sync"
    private const val KEY_PENDING = "pending_deletes"

    @Synchronized
    fun snapshot(context: Context): Set<String> =
        prefs(context).getStringSet(KEY_PENDING, emptySet())?.toSet() ?: emptySet()

    @Synchronized
    fun add(context: Context, key: String) {
        val value = key.trim()
        if (value.isEmpty()) return
        val next = snapshot(context).toMutableSet()
        if (next.add(value)) {
            prefs(context).edit().putStringSet(KEY_PENDING, next).apply()
        }
    }

    @Synchronized
    fun remove(context: Context, key: String) {
        val next = snapshot(context).toMutableSet()
        if (next.remove(key)) {
            prefs(context).edit().putStringSet(KEY_PENDING, next).apply()
        }
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
}

package com.example.voicenote.watch

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.example.voicenote.watch.util.Recordings
import java.io.File
import java.util.concurrent.TimeUnit

/** 把录音文件排队交给 WorkManager 上传（等待网络、失败自动退避重试）。 */
object UploadScheduler {
    private const val WORK_PREFIX = "watch-upload-"
    private const val TAG = "oss-upload"

    fun enqueue(context: Context, file: File) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(workDataOf(UploadWorker.KEY_FILE_NAME to file.name))
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .addTag(TAG)
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            WORK_PREFIX + file.name,
            ExistingWorkPolicy.KEEP,
            request
        )
        WatchState.uploadWaiting(context.getString(R.string.hint_upload_waiting))
    }

    /**
     * 启动时把上次异常退出留下的录音重新加入上传队列。
     * 正在录制的文件会被跳过。
     */
    fun enqueuePending(context: Context) {
        if (!WatchConfig.isReady) return
        val state = WatchState.state.value
        if (state.recording || state.starting) return

        val files = Recordings.dir(context).listFiles { file ->
            file.isFile &&
                file.extension.equals("m4a", ignoreCase = true) &&
                file.name != state.activeFileName
        } ?: return

        val now = System.currentTimeMillis()
        files.forEach { file ->
            // 刚写入的文件可能还在收尾，留一点缓冲。
            if (now - file.lastModified() < 2_000) return@forEach
            enqueue(context, file)
        }
    }
}

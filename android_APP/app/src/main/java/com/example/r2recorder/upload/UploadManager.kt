package com.example.r2recorder.upload

import android.content.Context
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf

object UploadManager {
    private const val WORK_NAME_PREFIX = "oss-upload-"

    fun enqueue(
        context: Context,
        recordingId: String,
        replace: Boolean = false,
        notifyUser: Boolean = false
    ) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(
                workDataOf(
                    UploadWorker.KEY_RECORDING_ID to recordingId,
                    UploadWorker.KEY_NOTIFY_USER to notifyUser
                )
            )
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .addTag("oss-upload")
            .build()

        WorkManager.getInstance(context).enqueueUniqueWork(
            WORK_NAME_PREFIX + recordingId,
            if (replace) ExistingWorkPolicy.REPLACE else ExistingWorkPolicy.KEEP,
            request
        )
    }

    fun retry(context: Context, recordingId: String) {
        enqueue(context, recordingId, replace = true)
    }

    fun cancel(context: Context, recordingId: String) {
        WorkManager.getInstance(context)
            .cancelUniqueWork(WORK_NAME_PREFIX + recordingId)
    }
}

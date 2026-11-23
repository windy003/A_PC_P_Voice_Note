package com.example.r2recorder.upload

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.UploadStatus
import java.util.concurrent.TimeUnit

object UploadManager {
    private const val WORK_NAME_PREFIX = "r2-upload-"

    fun enqueue(context: Context, recordingId: String, replace: Boolean = false) {
        val request = OneTimeWorkRequestBuilder<UploadWorker>()
            .setInputData(workDataOf(UploadWorker.KEY_RECORDING_ID to recordingId))
            .setConstraints(
                Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED)
                    .build()
            )
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .addTag("r2-upload")
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

    fun enqueuePendingLocal(context: Context): Int {
        val repository = RecordingRepository.get(context)
        var count = 0
        repository.snapshot()
            .filter { it.status == UploadStatus.LOCAL && it.localFile(context).exists() }
            .forEach {
                enqueue(context, it.id)
                count++
            }
        return count
    }

    fun retryAll(context: Context): Int {
        val repository = RecordingRepository.get(context)
        var count = 0
        repository.snapshot()
            .filter {
                (it.status == UploadStatus.LOCAL || it.status == UploadStatus.FAILED) &&
                    it.localFile(context).exists()
            }
            .forEach {
                repository.update(it.id) { recording ->
                    recording.copy(status = UploadStatus.LOCAL, error = null)
                }
                enqueue(context, it.id, replace = true)
                count++
            }
        return count
    }
}

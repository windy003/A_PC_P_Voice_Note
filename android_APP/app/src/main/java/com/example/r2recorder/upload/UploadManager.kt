package com.example.r2recorder.upload

import android.content.Context
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.workDataOf
import com.example.r2recorder.data.OssConfig
import com.example.r2recorder.data.Recording
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.data.UploadStatus
import java.util.concurrent.TimeUnit

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
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
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

    fun enqueuePendingLocal(context: Context): Int {
        val repository = RecordingRepository.get(context)
        val config = SettingsStore(context).configOrNull()
        var count = 0
        repository.snapshot().forEach { recording ->
            if (!recording.localFile(context).exists()) return@forEach
            var item = recording
            if (item.status == UploadStatus.UPLOADED &&
                config != null &&
                belongsToOtherStorage(item, config)
            ) {
                // 云端配置已切换（例如从 R2 迁移到 OSS），把已上传的记录重新上传。
                repository.update(item.id) {
                    it.copy(status = UploadStatus.LOCAL, error = null)
                }
                item = repository.get(item.id) ?: item
            }
            if (item.status == UploadStatus.LOCAL) {
                enqueue(context, item.id)
                count++
            }
        }
        return count
    }

    private fun belongsToOtherStorage(recording: Recording, config: OssConfig): Boolean =
        recording.remoteEndpoint != config.endpointHost ||
            recording.remoteBucket != config.bucket ||
            recording.remotePrefix != config.normalizedPrefix

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

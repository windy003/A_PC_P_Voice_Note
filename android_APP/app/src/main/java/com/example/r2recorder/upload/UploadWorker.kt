package com.example.r2recorder.upload

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.WorkerParameters
import com.example.r2recorder.R
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.data.UploadStatus
import com.example.r2recorder.r2.R2Client
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.IOException

class UploadWorker(
    appContext: Context,
    params: WorkerParameters
) : CoroutineWorker(appContext, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val id = inputData.getString(KEY_RECORDING_ID) ?: return@withContext Result.failure()
        val repository = RecordingRepository.get(applicationContext)
        val recording = repository.get(id) ?: return@withContext Result.failure()
        if (recording.status == UploadStatus.UPLOADED) return@withContext Result.success()

        val config = SettingsStore(applicationContext).configOrNull()
        if (config == null) {
            repository.update(id) {
                it.copy(
                    status = UploadStatus.FAILED,
                    error = applicationContext.getString(R.string.upload_error_no_config)
                )
            }
            return@withContext Result.failure()
        }

        val file = recording.localFile(applicationContext)
        if (!file.exists()) {
            repository.update(id) {
                it.copy(
                    status = UploadStatus.FAILED,
                    error = applicationContext.getString(R.string.upload_error_file_missing)
                )
            }
            return@withContext Result.failure()
        }

        repository.update(id) { it.copy(status = UploadStatus.UPLOADING, error = null) }
        val objectKey = config.objectKeyFor(recording.fileName)

        try {
            R2Client.upload(config, file, objectKey)
            repository.update(id) {
                it.copy(
                    status = UploadStatus.UPLOADED,
                    objectKey = objectKey,
                    remoteUrl = config.publicUrlFor(objectKey),
                    error = null
                )
            }
            Result.success()
        } catch (e: R2Client.R2Exception) {
            val message = applicationContext.getString(
                R.string.upload_error_http,
                e.code,
                e.detail
            )
            if (e.retryable && runAttemptCount < 2) {
                repository.update(id) {
                    it.copy(
                        status = UploadStatus.FAILED,
                        error = message + "；" + applicationContext.getString(R.string.upload_retry_wait)
                    )
                }
                Result.retry()
            } else {
                repository.update(id) { it.copy(status = UploadStatus.FAILED, error = message) }
                Result.failure()
            }
        } catch (e: IOException) {
            val message = applicationContext.getString(
                R.string.upload_error_network,
                e.message ?: applicationContext.getString(R.string.unknown_error)
            )
            if (runAttemptCount < 2) {
                repository.update(id) {
                    it.copy(
                        status = UploadStatus.FAILED,
                        error = message + "；" + applicationContext.getString(R.string.upload_retry_wait)
                    )
                }
                Result.retry()
            } else {
                repository.update(id) { it.copy(status = UploadStatus.FAILED, error = message) }
                Result.failure()
            }
        } catch (e: Exception) {
            val message = e.message ?: applicationContext.getString(R.string.unknown_error)
            repository.update(id) { it.copy(status = UploadStatus.FAILED, error = message) }
            Result.failure()
        }
    }

    companion object {
        const val KEY_RECORDING_ID = "recording_id"
    }
}

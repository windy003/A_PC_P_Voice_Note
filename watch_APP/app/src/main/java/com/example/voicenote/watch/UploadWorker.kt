package com.example.voicenote.watch

import android.content.Context
import androidx.work.Worker
import androidx.work.WorkerParameters
import com.example.voicenote.watch.oss.OssClient
import com.example.voicenote.watch.util.Recordings
import java.io.File
import java.io.IOException

/** 上传单个录音文件，成功后删除本地文件。 */
class UploadWorker(
    appContext: Context,
    params: WorkerParameters
) : Worker(appContext, params) {

    override fun doWork(): Result {
        val fileName = inputData.getString(KEY_FILE_NAME)
            ?: return Result.failure()
        val file = File(Recordings.dir(applicationContext), fileName)
        if (!file.isFile) {
            // 已经上传并删除，或录音被放弃。
            return Result.success()
        }

        val config = WatchConfig.oss
        if (!config.isComplete) {
            WatchState.uploadFailed(applicationContext.getString(R.string.err_upload_no_config))
            return Result.failure()
        }

        WatchState.uploading()
        return try {
            OssClient.upload(config, file, config.objectKeyFor(file.name))
            file.delete()
            WatchState.uploaded()
            Result.success()
        } catch (e: OssClient.OssException) {
            if (e.retryable) {
                WatchState.uploadWaiting(applicationContext.getString(R.string.err_upload_network))
                Result.retry()
            } else {
                WatchState.uploadFailed(e.message ?: e.detail)
                Result.failure()
            }
        } catch (e: IOException) {
            WatchState.uploadWaiting(applicationContext.getString(R.string.err_upload_network))
            Result.retry()
        } catch (e: Exception) {
            WatchState.uploadFailed(
                e.message ?: applicationContext.getString(R.string.status_upload_failed)
            )
            Result.failure()
        }
    }

    companion object {
        const val KEY_FILE_NAME = "file_name"
    }
}

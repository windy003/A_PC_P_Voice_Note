package com.example.voicenote.watch

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import androidx.core.content.ContextCompat
import com.example.voicenote.watch.util.Formats
import com.example.voicenote.watch.util.Recordings
import java.io.File

/**
 * 录音前台服务：录音结束后立即把文件交给 UploadScheduler 上传。
 * 手表端不维护本地录音列表，上传成功后文件会被删除。
 */
class RecordingService : Service() {

    private var recorder: MediaRecorder? = null
    private var currentFile: File? = null
    private var recording = false
    private var startedAtElapsed = 0L
    private var startedAtWallClock = 0L

    override fun onCreate() {
        super.onCreate()
        ensureChannel(this)
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> startRecording()
            ACTION_STOP -> finishRecording()
            else -> if (!recording) stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun startRecording() {
        if (recording) return

        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            WatchState.failed(getString(R.string.status_permission))
            stopSelf()
            return
        }

        startedAtWallClock = System.currentTimeMillis()
        try {
            startForegroundInternal(startedAtWallClock)
        } catch (e: Exception) {
            WatchState.failed(
                getString(
                    R.string.err_start_failed,
                    e.message ?: getString(R.string.status_upload_failed)
                )
            )
            stopSelf()
            return
        }

        try {
            val file = nextRecordingFile(Recordings.dir(this), startedAtWallClock)
            val newRecorder = createRecorder(file)
            newRecorder.start()

            recorder = newRecorder
            currentFile = file
            startedAtElapsed = SystemClock.elapsedRealtime()
            recording = true
            WatchState.started(startedAtElapsed, file.name)
        } catch (e: Exception) {
            recorder?.release()
            recorder = null
            currentFile?.delete()
            currentFile = null
            recording = false
            WatchState.failed(
                getString(
                    R.string.err_start_failed,
                    e.message ?: getString(R.string.status_upload_failed)
                )
            )
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
        }
    }

    private fun finishRecording() {
        if (!recording && recorder == null) {
            stopSelf()
            return
        }

        recording = false
        val activeRecorder = recorder
        val file = currentFile
        val startElapsed = startedAtElapsed
        recorder = null
        currentFile = null
        startedAtElapsed = 0L
        startedAtWallClock = 0L

        var stoppedCleanly = false
        try {
            activeRecorder?.stop()
            stoppedCleanly = true
        } catch (_: Exception) {
            stoppedCleanly = false
        } finally {
            activeRecorder?.release()
        }

        if (stoppedCleanly && file != null && file.exists() && file.length() > 0) {
            val durationMs = (SystemClock.elapsedRealtime() - startElapsed).coerceAtLeast(0)
            if (durationMs < 1_000) {
                file.delete()
                WatchState.failed(getString(R.string.err_too_short))
            } else {
                WatchState.stopped()
                UploadScheduler.enqueue(this, file)
            }
        } else {
            file?.delete()
            WatchState.failed(getString(R.string.err_save_failed))
        }

        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        if (recording) {
            finishRecording()
        }
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    private fun createRecorder(file: File): MediaRecorder {
        val newRecorder =
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(this) else MediaRecorder()
        return newRecorder.apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioChannels(1)
            setAudioSamplingRate(44_100)
            setAudioEncodingBitRate(128_000)
            setOutputFile(file.absolutePath)
            prepare()
        }
    }

    private fun nextRecordingFile(directory: File, timestamp: Long): File {
        val base = "rec-${Formats.fileTimestamp(timestamp)}"
        var file = File(directory, "$base.m4a")
        var index = 2
        while (file.exists()) {
            file = File(directory, "$base-$index.m4a")
            index++
        }
        return file
    }

    private fun startForegroundInternal(startWallClock: Long) {
        val notification = buildNotification(startWallClock)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun buildNotification(startWallClock: Long): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this,
            2,
            Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopAction = Notification.Action.Builder(
            Icon.createWithResource(this, R.drawable.ic_stop_white),
            getString(R.string.notification_stop),
            stopIntent
        ).build()

        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setColor(getColor(R.color.record_red))
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(getString(R.string.notification_text))
            .setContentIntent(openIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(true)
            .setUsesChronometer(true)
            .setWhen(startWallClock)
            .setCategory(Notification.CATEGORY_SERVICE)
            .setPriority(Notification.PRIORITY_LOW)
            .addAction(stopAction)
            .build()
    }

    companion object {
        const val ACTION_START = "com.example.voicenote.watch.action.START_RECORDING"
        const val ACTION_STOP = "com.example.voicenote.watch.action.STOP_RECORDING"
        private const val CHANNEL_ID = "recording"
        private const val NOTIFICATION_ID = 1001

        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val manager = context.getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL_ID) != null) return
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.notification_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = context.getString(R.string.notification_channel_description)
                setShowBadge(false)
                setSound(null, null)
                enableVibration(false)
            }
            manager.createNotificationChannel(channel)
        }
    }
}

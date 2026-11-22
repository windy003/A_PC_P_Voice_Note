package com.example.r2recorder.recorder

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
import android.media.MediaRecorder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.example.r2recorder.MainActivity
import com.example.r2recorder.R
import com.example.r2recorder.data.Recording
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.data.UploadStatus
import com.example.r2recorder.upload.UploadManager
import com.example.r2recorder.util.Formats
import java.io.File
import java.util.UUID

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
            Toast.makeText(this, R.string.record_permission_denied, Toast.LENGTH_SHORT).show()
            RecorderState.idle()
            stopSelf()
            return
        }

        startedAtWallClock = System.currentTimeMillis()
        try {
            startForegroundInternal(startedAtWallClock)
        } catch (e: Exception) {
            Toast.makeText(
                this,
                getString(R.string.record_start_failed, e.message ?: getString(R.string.unknown_error)),
                Toast.LENGTH_LONG
            ).show()
            stopSelf()
            return
        }

        try {
            val directory = Recording.recordingsDir(this)
            val file = nextRecordingFile(directory, startedAtWallClock)
            val newRecorder = createRecorder(file)
            newRecorder.start()

            recorder = newRecorder
            currentFile = file
            startedAtElapsed = SystemClock.elapsedRealtime()
            recording = true
            RecorderState.start(startedAtElapsed, file.name)
        } catch (e: Exception) {
            recorder?.release()
            recorder = null
            currentFile?.delete()
            currentFile = null
            recording = false
            RecorderState.idle()
            stopForeground(STOP_FOREGROUND_REMOVE)
            Toast.makeText(
                this,
                getString(R.string.record_start_failed, e.message ?: getString(R.string.unknown_error)),
                Toast.LENGTH_LONG
            ).show()
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
        val startWallClock = startedAtWallClock
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
            val item = Recording(
                id = UUID.randomUUID().toString(),
                fileName = file.name,
                createdAt = startWallClock,
                durationMs = durationMs,
                sizeBytes = file.length(),
                status = UploadStatus.LOCAL
            )
            RecordingRepository.get(this).add(item)
            if (SettingsStore(this).configOrNull() != null) {
                UploadManager.enqueue(this, item.id)
            }
            Toast.makeText(this, R.string.record_saved, Toast.LENGTH_SHORT).show()
        } else {
            val tooShort = startElapsed > 0 &&
                SystemClock.elapsedRealtime() - startElapsed < 1000
            file?.delete()
            Toast.makeText(
                this,
                if (tooShort) R.string.record_too_short else R.string.record_save_failed,
                Toast.LENGTH_SHORT
            ).show()
        }

        RecorderState.idle()
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
        val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE
        } else {
            0
        }
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(startWallClock),
            type
        )
    }

    private fun buildNotification(startWallClock: Long): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            1,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val stopIntent = PendingIntent.getService(
            this,
            2,
            Intent(this, RecordingService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_mic)
            .setColor(ContextCompat.getColor(this, R.color.record_red))
            .setContentTitle(getString(R.string.recording_notification_title))
            .setContentText(getString(R.string.recording_notification_text))
            .setContentIntent(openIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setShowWhen(true)
            .setUsesChronometer(true)
            .setWhen(startWallClock)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .addAction(
                R.drawable.ic_stop_small,
                getString(R.string.recording_notification_stop),
                stopIntent
            )
            .build()
    }

    companion object {
        const val ACTION_START = "com.example.r2recorder.action.START_RECORDING"
        const val ACTION_STOP = "com.example.r2recorder.action.STOP_RECORDING"
        const val CHANNEL_ID = "recording"
        private const val NOTIFICATION_ID = 1001

        fun ensureChannel(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val manager = context.getSystemService(NotificationManager::class.java)
            if (manager.getNotificationChannel(CHANNEL_ID) != null) return
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.recording_channel_name),
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = context.getString(R.string.recording_channel_description)
                setShowBadge(false)
                setSound(null, null)
                enableVibration(false)
            }
            manager.createNotificationChannel(channel)
        }
    }
}

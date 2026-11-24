package com.example.r2recorder

import android.Manifest
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.content.res.ColorStateList
import android.graphics.Color
import android.media.AudioAttributes
import android.media.MediaPlayer
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.isVisible
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.recyclerview.widget.LinearLayoutManager
import com.example.r2recorder.data.Recording
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.data.UploadStatus
import com.example.r2recorder.databinding.ActivityMainBinding
import com.example.r2recorder.recorder.RecorderState
import com.example.r2recorder.recorder.RecordingService
import com.example.r2recorder.sync.PendingDeletes
import com.example.r2recorder.sync.SyncManager
import com.example.r2recorder.ui.RecordingAdapter
import com.example.r2recorder.upload.UploadManager
import com.example.r2recorder.util.Formats
import com.example.r2recorder.util.Toasts
import com.google.android.material.snackbar.Snackbar
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class MainActivity : AppCompatActivity(), RecordingAdapter.Callbacks {

    private lateinit var binding: ActivityMainBinding
    private lateinit var repository: RecordingRepository
    private lateinit var settingsStore: SettingsStore
    private lateinit var adapter: RecordingAdapter

    private var player: MediaPlayer? = null
    private var playingId: String? = null
    private var tickerJob: Job? = null
    private var syncJob: Job? = null
    private var syncSuccessNotified = false
    private var startingRecording = false
    private var sortDescending = false

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val audioGranted = grants[Manifest.permission.RECORD_AUDIO] == true || hasAudioPermission()
        if (audioGranted) {
            startRecordingService()
        } else {
            showMessage(R.string.record_permission_denied, R.string.open_settings) {
                openAppSettings()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        repository = RecordingRepository.get(this)
        settingsStore = SettingsStore(this)
        adapter = RecordingAdapter(this)

        binding.recycler.layoutManager = LinearLayoutManager(this)
        binding.recycler.adapter = adapter

        binding.btnRecord.setOnClickListener { onRecordButtonClick() }
        binding.btnConfigure.setOnClickListener { openSettings() }
        binding.btnSort.setOnClickListener { toggleSort() }
        binding.toolbar.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.action_settings -> {
                    openSettings()
                    true
                }

                R.id.action_retry_all -> {
                    retryAllUploads()
                    true
                }

                else -> false
            }
        }

        repository.items.observe(this) { items ->
            renderItems(items)
        }

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                RecorderState.state.collectLatest { renderRecordingState(it) }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        val configured = settingsStore.configOrNull() != null
        binding.configBanner.isVisible = !configured
        if (configured) {
            UploadManager.enqueuePendingLocal(this)
            startCloudSync()
        }
    }

    override fun onStop() {
        tickerJob?.cancel()
        tickerJob = null
        releasePlayer()
        // 离开前台后重置，下次从后台回到应用时重新同步并提示一次。
        syncSuccessNotified = false
        super.onStop()
    }

    override fun onDestroy() {
        tickerJob?.cancel()
        player?.release()
        player = null
        super.onDestroy()
    }

    private fun onRecordButtonClick() {
        if (startingRecording) return
        if (RecorderState.state.value.isRecording) {
            stopRecordingService()
        } else {
            requestPermissionsAndStart()
        }
    }

    private fun requestPermissionsAndStart() {
        val missing = mutableListOf<String>()
        if (!hasAudioPermission()) {
            missing += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            missing += Manifest.permission.POST_NOTIFICATIONS
        }

        if (missing.isEmpty()) {
            startRecordingService()
        } else {
            permissionLauncher.launch(missing.toTypedArray())
        }
    }

    private fun hasAudioPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    private fun startRecordingService() {
        if (startingRecording) return
        startingRecording = true
        binding.btnRecord.isEnabled = false
        binding.btnRecord.setText(R.string.record_starting)

        try {
            val intent = Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START)
            ContextCompat.startForegroundService(this, intent)

            binding.root.postDelayed({
                if (!RecorderState.state.value.isRecording) {
                    startingRecording = false
                    renderRecordingState(RecorderState.state.value)
                }
            }, 2500)
        } catch (e: Exception) {
            startingRecording = false
            renderRecordingState(RecorderState.state.value)
            showMessage(getString(R.string.record_start_failed, e.message ?: getString(R.string.unknown_error)))
        }
    }

    private fun stopRecordingService() {
        binding.btnRecord.isEnabled = false
        try {
            startService(
                Intent(this, RecordingService::class.java)
                    .setAction(RecordingService.ACTION_STOP)
            )
        } catch (e: Exception) {
            renderRecordingState(RecorderState.state.value)
            showMessage(getString(R.string.record_save_failed))
        }
    }

    private fun renderItems(items: List<Recording>, scrollToTop: Boolean = false) {
        val ordered = sortRecordings(items)
        binding.tvCount.isVisible = ordered.isNotEmpty()
        binding.tvCount.text = getString(R.string.recording_count, ordered.size)
        binding.tvEmpty.isVisible = ordered.isEmpty()
        binding.btnSort.setText(
            if (sortDescending) R.string.sort_descending else R.string.sort_ascending
        )
        adapter.submitList(ordered) {
            // 切换排序后等 Diff 应用完成再置顶，避免列表随后又被拉回原来的位置。
            if (scrollToTop) {
                scrollListToTop()
            }
        }
    }

    private fun scrollListToTop() {
        val layoutManager = binding.recycler.layoutManager as? LinearLayoutManager
        if (layoutManager != null) {
            layoutManager.scrollToPositionWithOffset(0, 0)
        } else {
            binding.recycler.scrollToPosition(0)
        }
    }

    private fun sortRecordings(items: List<Recording>): List<Recording> {
        val ordered = items.sortedWith(
            compareBy<Recording> {
                Formats.filenameTimeMs(it.fileName) ?: it.createdAt
            }.thenBy { it.fileName }
        )
        return if (sortDescending) ordered.reversed() else ordered
    }

    private fun toggleSort() {
        sortDescending = !sortDescending
        renderItems(repository.snapshot(), scrollToTop = true)
        // 立即置顶一次，等列表提交后再置顶一次，确保每次都回到顶部。
        scrollListToTop()
    }

    private fun renderRecordingState(state: RecorderState.State) {
        if (state.isRecording) {
            startingRecording = false
        }
        if (startingRecording) return

        binding.btnRecord.isEnabled = true
        if (state.isRecording) {
            binding.tvStatus.setText(R.string.status_recording)
            binding.btnRecord.setText(R.string.record_stop)
            binding.btnRecord.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(this, R.color.record_red)
            )
            binding.btnRecord.setTextColor(Color.WHITE)
            startTicker(state.startedAtElapsed)
        } else {
            binding.tvStatus.setText(R.string.status_ready)
            binding.tvTimer.setText(R.string.timer_idle)
            binding.btnRecord.setText(R.string.record_start)
            binding.btnRecord.backgroundTintList = ColorStateList.valueOf(
                ContextCompat.getColor(this, R.color.md_theme_primary)
            )
            binding.btnRecord.setTextColor(
                ContextCompat.getColor(this, R.color.md_theme_onPrimary)
            )
            tickerJob?.cancel()
            tickerJob = null
        }
    }

    private fun startTicker(startedAtElapsed: Long) {
        tickerJob?.cancel()
        tickerJob = lifecycleScope.launch {
            while (isActive) {
                val elapsed = (SystemClock.elapsedRealtime() - startedAtElapsed).coerceAtLeast(0)
                binding.tvTimer.text = Formats.duration(elapsed)
                delay(500)
            }
        }
    }

    override fun onPlay(recording: Recording) {
        if (playingId == recording.id) {
            releasePlayer()
            return
        }
        releasePlayer()

        val file = recording.localFile(this)
        if (!file.exists()) {
            showMessage(R.string.playback_failed)
            return
        }

        val mediaPlayer = MediaPlayer()
        try {
            mediaPlayer.setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
            )
            mediaPlayer.setDataSource(file.absolutePath)
            mediaPlayer.setOnCompletionListener { releasePlayer() }
            mediaPlayer.setOnErrorListener { _, _, _ ->
                releasePlayer()
                showMessage(R.string.playback_failed)
                true
            }
            mediaPlayer.prepare()
            mediaPlayer.start()
            player = mediaPlayer
            playingId = recording.id
            adapter.setPlayingId(recording.id)
        } catch (_: Exception) {
            mediaPlayer.release()
            showMessage(R.string.playback_failed)
        }
    }

    private fun releasePlayer() {
        try {
            player?.stop()
        } catch (_: Exception) {
            // Player may not have reached the started state.
        }
        player?.release()
        player = null
        playingId = null
        adapter.setPlayingId(null)
    }

    override fun onOpen(recording: Recording) {
        val url = recording.remoteUrl ?: return
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
        } catch (_: ActivityNotFoundException) {
            showMessage(R.string.open_url_failed)
        }
    }

    override fun onRetry(recording: Recording) {
        if (settingsStore.configOrNull() == null) {
            showMessage(R.string.need_config, R.string.open_settings) { openSettings() }
            return
        }
        repository.update(recording.id) {
            it.copy(status = com.example.r2recorder.data.UploadStatus.LOCAL, error = null)
        }
        UploadManager.retry(this, recording.id)
        Toasts.show(this, getString(R.string.retry_queued))
    }

    override fun onDelete(recording: Recording) {
        AlertDialog.Builder(this)
            .setTitle(R.string.delete_title)
            .setMessage(R.string.delete_message)
            .setNegativeButton(R.string.cancel, null)
            .setPositiveButton(R.string.delete_confirm) { _, _ ->
                deleteRecording(recording)
            }
            .show()
    }

    private fun deleteRecording(recording: Recording) {
        if (playingId == recording.id) {
            releasePlayer()
        }
        UploadManager.cancel(this, recording.id)

        val config = settingsStore.configOrNull()
        val remoteKey = recording.objectKey ?: config?.objectKeyFor(recording.fileName)
        val wasRemote = !recording.objectKey.isNullOrBlank() ||
            recording.status == UploadStatus.UPLOADED ||
            recording.status == UploadStatus.UPLOADING

        recording.localFile(this).delete()
        repository.delete(recording.id)

        if (wasRemote && remoteKey != null) {
            // 先落盘再删云端：即使进程中途退出，下次同步也会继续删除。
            PendingDeletes.add(this, remoteKey)
            lifecycleScope.launch {
                val (text, isError) = withContext(Dispatchers.IO) {
                    SyncManager.deleteCloudCopy(this@MainActivity, remoteKey)
                }
                if (isFinishing || isDestroyed) return@launch
                Snackbar.make(
                    binding.root,
                    Toasts.withTotalCount(this@MainActivity, text),
                    if (isError) Snackbar.LENGTH_LONG else Snackbar.LENGTH_SHORT
                ).show()
            }
        } else if (wasRemote) {
            showMessage(R.string.delete_cloud_unavailable)
        } else {
            showMessage(R.string.delete_local_only)
        }
    }

    private fun startCloudSync() {
        if (syncJob?.isActive == true) return
        syncJob = lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) {
                SyncManager.sync(this@MainActivity)
            }
            if (isFinishing || isDestroyed) return@launch
            if (!lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)) return@launch
            // 同一次前台停留期间 onResume 可能被多次触发（例如权限弹窗），
            // 成功提示只显示一次；每次从后台回到应用后重新计数。
            if (!result.connected || syncSuccessNotified) return@launch
            syncSuccessNotified = true
            val (text, _) = SyncManager.formatResult(result)
            Snackbar.make(
                binding.root,
                Toasts.withTotalCount(this@MainActivity, text),
                Snackbar.LENGTH_SHORT
            ).show()
        }
    }

    private fun retryAllUploads() {
        if (settingsStore.configOrNull() == null) {
            showMessage(R.string.need_config, R.string.open_settings) { openSettings() }
            return
        }
        val count = UploadManager.retryAll(this)
        if (count == 0) {
            showMessage(R.string.retry_all_empty)
        } else {
            Toasts.show(this, getString(R.string.retry_all_queued))
        }
    }

    private fun openSettings() {
        startActivity(Intent(this, SettingsActivity::class.java))
    }

    private fun openAppSettings() {
        startActivity(
            Intent(
                Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                Uri.parse("package:$packageName")
            )
        )
    }

    private fun showMessage(messageRes: Int, actionRes: Int? = null, action: (() -> Unit)? = null) {
        showMessage(getString(messageRes), actionRes, action)
    }

    private fun showMessage(
        message: String,
        actionRes: Int? = null,
        action: (() -> Unit)? = null
    ) {
        val bar = Snackbar.make(
            binding.root,
            Toasts.withTotalCount(this, message),
            Snackbar.LENGTH_LONG
        )
        if (actionRes != null && action != null) {
            bar.setAction(actionRes) { action() }
        }
        bar.show()
    }
}

package com.example.voicenote.watch

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.example.voicenote.watch.databinding.ActivityMainBinding
import com.example.voicenote.watch.util.Formats
import kotlinx.coroutines.launch

/**
 * 手表端唯一的界面：一个大按钮，点按开始录音，再点按停止并上传。
 */
class MainActivity : ComponentActivity() {

    private lateinit var binding: ActivityMainBinding
    private val handler = Handler(Looper.getMainLooper())
    private var keepScreenOn = false

    private val ticker = object : Runnable {
        override fun run() {
            render(WatchState.state.value)
            handler.postDelayed(this, 500L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        binding.recordButton.setOnClickListener { toggleRecording() }

        if (!WatchConfig.isReady) {
            WatchState.setMessage(getString(R.string.hint_no_config))
        }
        ensurePermissions()
        UploadScheduler.enqueuePending(this)

        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                WatchState.state.collect { render(it) }
            }
        }
    }

    override fun onStart() {
        super.onStart()
        handler.post(ticker)
    }

    override fun onStop() {
        handler.removeCallbacks(ticker)
        setKeepScreenOn(false)
        super.onStop()
    }

    override fun onDestroy() {
        handler.removeCallbacks(ticker)
        super.onDestroy()
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQUEST_PERMISSIONS) {
            render(WatchState.state.value)
        }
    }

    private fun toggleRecording() {
        val state = WatchState.state.value
        if (state.recording || state.starting) {
            startService(
                Intent(this, RecordingService::class.java)
                    .setAction(RecordingService.ACTION_STOP)
            )
            return
        }

        if (!hasRecordPermission()) {
            ensurePermissions()
            return
        }
        if (!WatchConfig.isReady) {
            WatchState.setMessage(getString(R.string.hint_no_config))
            return
        }

        WatchState.starting()
        ContextCompat.startForegroundService(
            this,
            Intent(this, RecordingService::class.java)
                .setAction(RecordingService.ACTION_START)
        )
    }

    private fun render(state: WatchState.State) {
        if (state.recording) {
            binding.statusText.setText(R.string.status_recording)
            binding.statusText.setTextColor(getColor(R.color.status_error))
            binding.recordButton.setImageResource(R.drawable.ic_stop_white)
            binding.recordButton.setBackgroundResource(R.drawable.bg_record_button_recording)
            binding.recordButton.contentDescription = getString(R.string.action_stop)
            val elapsed = (SystemClock.elapsedRealtime() - state.startedAtElapsed)
                .coerceAtLeast(0L)
            binding.detailText.text = Formats.duration(elapsed)
            binding.detailText.setTextColor(getColor(R.color.watch_text_primary))
            setKeepScreenOn(true)
            return
        }

        binding.recordButton.setImageResource(R.drawable.ic_mic_white)
        binding.recordButton.setBackgroundResource(R.drawable.bg_record_button)
        binding.recordButton.contentDescription = getString(R.string.action_start)
        setKeepScreenOn(false)

        when {
            state.starting -> showStatus(
                R.string.status_starting,
                R.string.hint_ready,
                R.color.watch_text_secondary
            )

            !hasRecordPermission() -> showStatus(
                R.string.status_permission,
                R.string.hint_permission,
                R.color.status_error
            )

            !WatchConfig.isReady -> showStatus(
                R.string.status_no_config,
                R.string.hint_no_config,
                R.color.status_error
            )

            state.upload == WatchState.UploadPhase.UPLOADING -> showStatus(
                R.string.status_uploading,
                R.string.hint_uploading,
                R.color.status_pending
            )

            state.upload == WatchState.UploadPhase.WAITING -> showStatus(
                R.string.status_upload_waiting,
                R.string.hint_upload_waiting,
                R.color.status_pending
            )

            state.upload == WatchState.UploadPhase.DONE -> showStatus(
                R.string.status_upload_done,
                R.string.hint_upload_done,
                R.color.status_ok
            )

            state.upload == WatchState.UploadPhase.FAILED -> {
                binding.statusText.setText(R.string.status_upload_failed)
                binding.statusText.setTextColor(getColor(R.color.status_error))
                binding.detailText.text = state.message
                    .ifBlank { getString(R.string.err_upload_network) }
                binding.detailText.setTextColor(getColor(R.color.watch_text_tertiary))
            }

            else -> {
                binding.statusText.setText(R.string.status_ready)
                binding.statusText.setTextColor(getColor(R.color.watch_text_secondary))
                binding.detailText.text = state.message
                    .ifBlank { getString(R.string.hint_tap_to_start) }
                binding.detailText.setTextColor(getColor(R.color.watch_text_tertiary))
            }
        }
    }

    private fun showStatus(statusRes: Int, detailRes: Int, colorRes: Int) {
        binding.statusText.setText(statusRes)
        binding.statusText.setTextColor(getColor(colorRes))
        binding.detailText.setText(detailRes)
        binding.detailText.setTextColor(getColor(R.color.watch_text_tertiary))
    }

    private fun setKeepScreenOn(on: Boolean) {
        if (on == keepScreenOn) return
        keepScreenOn = on
        if (on) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun ensurePermissions() {
        val missing = requiredPermissions().filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }
        if (missing.isNotEmpty()) {
            requestPermissions(missing.toTypedArray(), REQUEST_PERMISSIONS)
        }
    }

    private fun hasRecordPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED

    private fun requiredPermissions(): List<String> = buildList {
        add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            add(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    companion object {
        private const val REQUEST_PERMISSIONS = 100
    }
}

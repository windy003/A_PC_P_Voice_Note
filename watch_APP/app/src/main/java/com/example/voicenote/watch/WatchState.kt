package com.example.voicenote.watch

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * 录音与上传的界面状态。录音服务、上传 Worker 和 Activity
 * 运行在同一个进程里，用这个单例交换状态。
 */
object WatchState {
    enum class UploadPhase {
        IDLE,
        WAITING,
        UPLOADING,
        DONE,
        FAILED
    }

    data class State(
        val recording: Boolean = false,
        val starting: Boolean = false,
        val startedAtElapsed: Long = 0L,
        val activeFileName: String = "",
        val upload: UploadPhase = UploadPhase.IDLE,
        val message: String = ""
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun starting() = _state.update {
        it.copy(
            starting = true,
            recording = false,
            upload = UploadPhase.IDLE,
            message = ""
        )
    }

    fun started(startedAtElapsed: Long, fileName: String) = _state.update {
        it.copy(
            recording = true,
            starting = false,
            startedAtElapsed = startedAtElapsed,
            activeFileName = fileName,
            upload = UploadPhase.IDLE,
            message = ""
        )
    }

    fun stopped() = _state.update {
        it.copy(
            recording = false,
            starting = false,
            startedAtElapsed = 0L,
            activeFileName = ""
        )
    }

    fun failed(message: String) = _state.update {
        it.copy(
            recording = false,
            starting = false,
            startedAtElapsed = 0L,
            activeFileName = "",
            upload = UploadPhase.IDLE,
            message = message
        )
    }

    fun setMessage(message: String) = _state.update { it.copy(message = message) }

    fun uploadWaiting(message: String) = _state.update {
        it.copy(upload = UploadPhase.WAITING, message = message)
    }

    fun uploading() = _state.update {
        it.copy(upload = UploadPhase.UPLOADING, message = "")
    }

    fun uploaded() = _state.update {
        it.copy(upload = UploadPhase.DONE, message = "")
    }

    fun uploadFailed(message: String) = _state.update {
        it.copy(upload = UploadPhase.FAILED, message = message)
    }
}

package com.example.r2recorder.recorder

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

object RecorderState {
    data class State(
        val isRecording: Boolean = false,
        val startedAtElapsed: Long = 0L,
        val fileName: String? = null
    )

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state.asStateFlow()

    fun start(startedAtElapsed: Long, fileName: String) {
        _state.value = State(isRecording = true, startedAtElapsed = startedAtElapsed, fileName = fileName)
    }

    fun idle() {
        _state.value = State()
    }
}

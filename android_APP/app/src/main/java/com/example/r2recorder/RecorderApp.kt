package com.example.r2recorder

import android.app.Application
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.recorder.RecordingService

class RecorderApp : Application() {
    override fun onCreate() {
        super.onCreate()
        RecordingService.ensureChannel(this)
        // Load metadata once on the main thread so the first UI render has data.
        RecordingRepository.get(this)
    }
}

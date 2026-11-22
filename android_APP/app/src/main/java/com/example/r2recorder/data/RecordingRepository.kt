package com.example.r2recorder.data

import android.content.Context
import androidx.lifecycle.LiveData
import androidx.lifecycle.MutableLiveData
import org.json.JSONArray
import java.io.File

class RecordingRepository private constructor(context: Context) {
    private val appContext = context.applicationContext
    private val metadataFile = File(appContext.filesDir, "recordings.json")
    private val lock = Any()
    private var current: List<Recording> = emptyList()

    private val _items = MutableLiveData<List<Recording>>(emptyList())
    val items: LiveData<List<Recording>> = _items

    init {
        current = readFromDisk()
        _items.value = current
    }

    fun snapshot(): List<Recording> = synchronized(lock) { current }

    fun get(id: String): Recording? = synchronized(lock) { current.firstOrNull { it.id == id } }

    fun add(recording: Recording) {
        synchronized(lock) {
            current = (listOf(recording) + current).sortedByDescending { it.createdAt }
            persistLocked()
            _items.postValue(current)
        }
    }

    fun update(id: String, transform: (Recording) -> Recording) {
        synchronized(lock) {
            var changed = false
            current = current.map {
                if (it.id == id) {
                    changed = true
                    transform(it)
                } else {
                    it
                }
            }
            if (changed) {
                persistLocked()
                _items.postValue(current)
            }
        }
    }

    fun delete(id: String) {
        synchronized(lock) {
            val next = current.filterNot { it.id == id }
            if (next.size != current.size) {
                current = next
                persistLocked()
                _items.postValue(current)
            }
        }
    }

    private fun readFromDisk(): List<Recording> {
        if (!metadataFile.exists()) return emptyList()
        return try {
            val array = JSONArray(metadataFile.readText())
            (0 until array.length()).mapNotNull { index ->
                Recording.fromJson(array.optJSONObject(index) ?: return@mapNotNull null)
            }.sortedByDescending { it.createdAt }
        } catch (_: Exception) {
            emptyList()
        }
    }

    private fun persistLocked() {
        try {
            val array = JSONArray()
            current.forEach { array.put(it.toJson()) }
            val json = array.toString()
            val temp = File(appContext.filesDir, "recordings.json.tmp")
            temp.writeText(json)
            if (!temp.renameTo(metadataFile)) {
                metadataFile.writeText(json)
                temp.delete()
            }
        } catch (_: Exception) {
            // Keep the in-memory list usable even if disk persistence fails.
        }
    }

    companion object {
        @Volatile
        private var instance: RecordingRepository? = null

        fun get(context: Context): RecordingRepository {
            return instance ?: synchronized(this) {
                instance ?: RecordingRepository(context).also { instance = it }
            }
        }
    }
}

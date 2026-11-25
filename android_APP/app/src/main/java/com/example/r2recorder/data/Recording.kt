package com.example.r2recorder.data

import android.content.Context
import org.json.JSONObject
import java.io.File

enum class UploadStatus {
    LOCAL,
    UPLOADING,
    UPLOADED,
    FAILED
}

data class Recording(
    val id: String,
    val fileName: String,
    val createdAt: Long,
    val durationMs: Long,
    val sizeBytes: Long,
    val status: UploadStatus,
    val objectKey: String? = null,
    val remoteUrl: String? = null,
    val error: String? = null,
    val remoteBucket: String? = null,
    val remotePrefix: String? = null,
    val remoteEndpoint: String? = null
) {
    fun localFile(context: Context): File = File(recordingsDir(context), fileName)

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("fileName", fileName)
        put("createdAt", createdAt)
        put("durationMs", durationMs)
        put("sizeBytes", sizeBytes)
        put("status", status.name)
        put("objectKey", objectKey ?: JSONObject.NULL)
        put("remoteUrl", remoteUrl ?: JSONObject.NULL)
        put("error", error ?: JSONObject.NULL)
        put("remoteBucket", remoteBucket ?: JSONObject.NULL)
        put("remotePrefix", remotePrefix ?: JSONObject.NULL)
        put("remoteEndpoint", remoteEndpoint ?: JSONObject.NULL)
    }

    companion object {
        fun recordingsDir(context: Context): File =
            File(context.filesDir, "recordings").apply { mkdirs() }

        fun fromJson(json: JSONObject): Recording? {
            return try {
                Recording(
                    id = json.getString("id"),
                    fileName = json.getString("fileName"),
                    createdAt = json.optLong("createdAt"),
                    durationMs = json.optLong("durationMs"),
                    sizeBytes = json.optLong("sizeBytes"),
                    status = runCatching { UploadStatus.valueOf(json.optString("status")) }
                        .getOrDefault(UploadStatus.LOCAL),
                    objectKey = json.optStringOrNull("objectKey"),
                    remoteUrl = json.optStringOrNull("remoteUrl"),
                    error = json.optStringOrNull("error"),
                    remoteBucket = json.optStringOrNull("remoteBucket"),
                    remotePrefix = json.optStringOrNull("remotePrefix"),
                    remoteEndpoint = json.optStringOrNull("remoteEndpoint")
                )
            } catch (_: Exception) {
                null
            }
        }

        private fun JSONObject.optStringOrNull(name: String): String? =
            if (has(name) && !isNull(name)) getString(name) else null
    }
}

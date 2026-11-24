package com.example.r2recorder.sync

import android.content.Context
import android.media.MediaMetadataRetriever
import android.util.Log
import com.example.r2recorder.data.R2Config
import com.example.r2recorder.data.Recording
import com.example.r2recorder.data.RecordingRepository
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.data.UploadStatus
import com.example.r2recorder.r2.R2Client
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Locale

data class SyncResult(
    val configured: Boolean,
    val connected: Boolean,
    val error: String? = null,
    val downloaded: Int = 0,
    val deletedLocal: Int = 0,
    val downloadFailed: Int = 0,
    val pendingFailed: Int = 0
)

/**
 * 打开应用时执行一次“云端 -> 本地”的增量同步。
 * 云端有、本地没有 -> 下载；本地已上传、云端没有 -> 删除本地。
 */
object SyncManager {
    private const val TAG = "SyncManager"
    private val AUDIO_SUFFIXES = setOf("m4a", "mp4", "aac", "wav", "mp3", "flac", "ogg")
    private val CREATED_AT_REGEX = Regex("rec-(\\d{8})-(\\d{6})")
    private val INVALID_NAME_REGEX = Regex("[\\\\/:*?\"<>|\\x00-\\x1f]")

    fun sync(context: Context): SyncResult {
        val repository = RecordingRepository.get(context)
        val config = SettingsStore(context).configOrNull()
            ?: return SyncResult(configured = false, connected = false)

        val failedPending = mutableSetOf<String>()
        for (key in PendingDeletes.snapshot(context)) {
            try {
                R2Client.deleteObject(config, key)
                PendingDeletes.remove(context, key)
            } catch (e: Exception) {
                failedPending.add(key)
                Log.w(TAG, "云端删除失败：$key", e)
            }
        }

        // 在拉取云端列表之前记录本地状态，避免上传中途完成的文件被误删。
        val localUploaded = mutableMapOf<String, Recording>()
        val localKeys = mutableSetOf<String>()
        for (recording in repository.snapshot()) {
            val key = recording.objectKey ?: config.objectKeyFor(recording.fileName)
            localKeys.add(key)
            if (recording.status == UploadStatus.UPLOADED) {
                localUploaded[key] = recording
            }
        }

        val objects = try {
            R2Client.listObjects(config, config.normalizedPrefix)
        } catch (e: Exception) {
            return SyncResult(
                configured = true,
                connected = false,
                error = e.message ?: "网络错误",
                pendingFailed = failedPending.size
            )
        }

        // 云端已经不存在的待删除对象说明删除目标已达成，直接清理，避免永久重试。
        val remoteKeys = objects.mapTo(mutableSetOf()) { it.key }
        for (key in failedPending.toList()) {
            if (key.startsWith(config.normalizedPrefix) && key !in remoteKeys) {
                PendingDeletes.remove(context, key)
                failedPending.remove(key)
                Log.i(TAG, "待删除对象已不在云端，清理记录：$key")
            }
        }
        val pendingFailed = failedPending.size
        val remote = objects
            .filter { suffixOf(it.key) in AUDIO_SUFFIXES }
            .associateBy { it.key }

        // 老版本记录没有 bucket / prefix 信息，确认云端存在后补上。
        for ((key, recording) in localUploaded) {
            if (recording.remoteBucket == null &&
                recording.remotePrefix == null &&
                key in remote
            ) {
                repository.update(recording.id) {
                    it.copy(
                        remoteBucket = config.bucket,
                        remotePrefix = config.normalizedPrefix
                    )
                }
            }
        }

        var downloaded = 0
        var downloadFailed = 0
        val pendingKeys = PendingDeletes.snapshot(context)
        for ((key, remoteObject) in remote) {
            if (key in localKeys || key in pendingKeys) continue
            try {
                downloadOne(context, config, remoteObject, repository)
                localKeys.add(key)
                downloaded++
            } catch (_: Exception) {
                downloadFailed++
            }
        }

        var deletedLocal = 0
        val pendingNow = PendingDeletes.snapshot(context)
        for ((key, recording) in localUploaded) {
            if (key in remote || key in pendingNow) continue
            if (recording.remoteBucket != config.bucket ||
                recording.remotePrefix != config.normalizedPrefix
            ) {
                // 属于另一套 R2 配置，不能因为当前前缀看不到就删本地。
                continue
            }
            val exists = try {
                R2Client.objectExists(config, key)
            } catch (_: Exception) {
                null
            }
            if (exists == false) {
                recording.localFile(context).delete()
                repository.delete(recording.id)
                deletedLocal++
            }
        }

        return SyncResult(
            configured = true,
            connected = true,
            downloaded = downloaded,
            deletedLocal = deletedLocal,
            downloadFailed = downloadFailed,
            pendingFailed = pendingFailed
        )
    }

    fun deleteCloudCopy(context: Context, objectKey: String): Pair<String, Boolean> {
        val config = SettingsStore(context).configOrNull()
        if (config == null) {
            PendingDeletes.add(context, objectKey)
            return "已删除本地；未配置 R2，云端副本暂未删除" to true
        }
        return try {
            R2Client.deleteObject(config, objectKey)
            PendingDeletes.remove(context, objectKey)
            "已删除本地和云端" to false
        } catch (e: Exception) {
            Log.w(TAG, "立即删除云端对象失败：$objectKey", e)
            // 对象本来就不存在时 R2 可能返回非 404 的错误；确认一次，避免留下无效待删除记录。
            val stillExists = runCatching {
                R2Client.objectExists(config, objectKey)
            }.getOrNull()
            if (stillExists == false) {
                PendingDeletes.remove(context, objectKey)
                return "已删除本地和云端" to false
            }
            PendingDeletes.add(context, objectKey)
            "已删除本地；云端删除失败，将在下次同步重试" to true
        }
    }

    fun formatResult(result: SyncResult): Pair<String, Boolean> {
        if (!result.configured) {
            return "云端同步未执行：尚未配置 R2" to false
        }
        if (!result.connected) {
            return "云端连接失败：${result.error ?: "未知错误"}" to true
        }
        val builder = StringBuilder()
        builder.append("云端连接成功 · 下行同步完成：")
        builder.append("新增 ${result.downloaded} 条，删除 ${result.deletedLocal} 条")
        if (result.downloadFailed > 0) {
            builder.append("；${result.downloadFailed} 条下载失败")
        }
        if (result.pendingFailed > 0) {
            builder.append("；${result.pendingFailed} 条云端删除待重试")
        }
        return builder.toString() to false
    }

    private fun downloadOne(
        context: Context,
        config: R2Config,
        remoteObject: R2Client.RemoteObject,
        repository: RecordingRepository
    ) {
        val desired = safeFileName(remoteObject.key.substringAfterLast('/'))
        val file = uniqueFile(context, repository, desired, remoteObject.key)
        R2Client.download(config, remoteObject.key, file)

        val record = Recording(
            id = "sync-" + sha256Hex(remoteObject.key).take(16),
            fileName = file.name,
            createdAt = createdAtFromKey(remoteObject.key)
                ?: remoteObject.lastModifiedMs.takeIf { it > 0 }
                ?: System.currentTimeMillis(),
            durationMs = probeDurationMs(file),
            sizeBytes = file.length(),
            status = UploadStatus.UPLOADED,
            objectKey = remoteObject.key,
            remoteUrl = config.publicUrlFor(remoteObject.key),
            error = null,
            remoteBucket = config.bucket,
            remotePrefix = config.normalizedPrefix
        )
        repository.add(record)
    }

    private fun uniqueFile(
        context: Context,
        repository: RecordingRepository,
        desired: String,
        key: String
    ): File {
        val directory = Recording.recordingsDir(context)
        val names = repository.snapshot().mapTo(mutableSetOf()) { it.fileName }
        if (desired !in names && !File(directory, desired).exists()) {
            return File(directory, desired)
        }
        val dot = desired.lastIndexOf('.')
        val stem = if (dot > 0) desired.substring(0, dot) else desired
        val extension = if (dot > 0) desired.substring(dot) else ""
        val token = sha256Hex(key).take(6)
        var candidate = "$stem-$token$extension"
        var index = 2
        while (candidate in names || File(directory, candidate).exists()) {
            candidate = "$stem-$token-$index$extension"
            index++
        }
        return File(directory, candidate)
    }

    private fun probeDurationMs(file: File): Long {
        var retriever: MediaMetadataRetriever? = null
        return try {
            retriever = MediaMetadataRetriever()
            retriever.setDataSource(file.absolutePath)
            retriever.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION)
                ?.toLongOrNull()
                ?.coerceAtLeast(0)
                ?: 0L
        } catch (_: Exception) {
            0L
        } finally {
            try {
                retriever?.release()
            } catch (_: Exception) {
                // Ignore release failures.
            }
        }
    }

    private fun createdAtFromKey(key: String): Long? {
        val match = CREATED_AT_REGEX.find(key.substringAfterLast('/')) ?: return null
        return try {
            SimpleDateFormat("yyyyMMddHHmmss", Locale.US)
                .apply { isLenient = false }
                .parse(match.groupValues[1] + match.groupValues[2])
                ?.time
        } catch (_: Exception) {
            null
        }
    }

    private fun suffixOf(key: String): String {
        val name = key.substringAfterLast('/')
        val dot = name.lastIndexOf('.')
        return if (dot >= 0) name.substring(dot + 1).lowercase(Locale.US) else ""
    }

    private fun safeFileName(name: String): String {
        val cleaned = name.replace(INVALID_NAME_REGEX, "_").trim().trimEnd('.')
        return cleaned.ifEmpty { "recording" }
    }

    private fun sha256Hex(value: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8))
        return buildString(digest.size * 2) {
            for (byte in digest) {
                append(String.format(Locale.US, "%02x", byte.toInt() and 0xFF))
            }
        }
    }
}

package com.example.r2recorder.data

import java.net.URLEncoder

data class R2Config(
    val accountId: String,
    val bucket: String,
    val accessKeyId: String,
    val secretAccessKey: String,
    val publicBaseUrl: String = "",
    val keyPrefix: String = DEFAULT_PREFIX
) {
    val endpointHost: String
        get() = "$accountId.r2.cloudflarestorage.com"

    private val normalizedPrefix: String
        get() {
            var prefix = keyPrefix.trim().replace('\\', '/')
            while (prefix.startsWith("/")) prefix = prefix.substring(1)
            if (prefix.isEmpty()) return DEFAULT_PREFIX
            if (!prefix.endsWith("/")) prefix += "/"
            return prefix
        }

    fun objectKeyFor(fileName: String): String = normalizedPrefix + fileName

    fun publicUrlFor(objectKey: String): String? {
        val base = publicBaseUrl.trim().trimEnd('/')
        if (base.isEmpty()) return null
        val encodedKey = objectKey.split("/").joinToString("/") { encodeSegment(it) }
        return "$base/$encodedKey"
    }

    companion object {
        const val DEFAULT_PREFIX = "recordings/"

        private fun encodeSegment(segment: String): String =
            URLEncoder.encode(segment, "UTF-8").replace("+", "%20")
    }
}

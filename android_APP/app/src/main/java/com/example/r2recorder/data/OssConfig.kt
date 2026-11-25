package com.example.r2recorder.data

import java.net.URLEncoder
import java.util.Locale

data class OssConfig(
    val endpoint: String,
    val bucket: String,
    val accessKeyId: String,
    val accessKeySecret: String,
    val region: String = "",
    val publicBaseUrl: String = "",
    val keyPrefix: String = DEFAULT_PREFIX
) {
    /** 去掉协议与末尾斜杠后的 Endpoint 主机名。 */
    val endpointHost: String
        get() {
            var host = endpoint.trim()
            if (host.startsWith("https://", ignoreCase = true)) {
                host = host.substring(8)
            } else if (host.startsWith("http://", ignoreCase = true)) {
                host = host.substring(7)
            }
            return host.trimEnd('/').substringBefore('/')
        }

    /** V4 签名所需的地域 ID；留空时从标准 Endpoint 推断。 */
    val resolvedRegion: String
        get() {
            val explicit = region.trim()
            if (explicit.isNotEmpty()) return explicit
            val label = endpointHost.lowercase(Locale.US)
                .split('.')
                .firstOrNull { it.startsWith("oss-") } ?: return ""
            var value = label.removePrefix("oss-")
            if (value.endsWith("-internal")) value = value.removeSuffix("-internal")
            if (value.isEmpty() || value.startsWith("accelerate")) return ""
            return value
        }

    /** 虚拟主机风格的请求域名：Bucket.Endpoint。 */
    val requestHost: String
        get() {
            val host = endpointHost
            val prefix = bucket.lowercase(Locale.US) + "."
            return if (host.lowercase(Locale.US).startsWith(prefix)) host else "$bucket.$host"
        }

    val normalizedPrefix: String
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
        val encoded = objectKey.split('/').joinToString("/") { encodeSegment(it) }
        return "$base/$encoded"
    }

    val isComplete: Boolean
        get() = endpoint.isNotBlank() &&
            resolvedRegion.isNotBlank() &&
            bucket.isNotBlank() &&
            accessKeyId.isNotBlank() &&
            accessKeySecret.isNotBlank()

    companion object {
        const val DEFAULT_PREFIX = "recordings/"

        private fun encodeSegment(segment: String): String =
            URLEncoder.encode(segment, "UTF-8").replace("+", "%20")
    }
}

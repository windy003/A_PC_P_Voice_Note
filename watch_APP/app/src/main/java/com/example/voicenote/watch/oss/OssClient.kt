package com.example.voicenote.watch.oss

import com.example.voicenote.watch.data.OssConfig
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.TreeMap
import java.util.concurrent.TimeUnit
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * 阿里云 OSS 的最小 REST 客户端，使用 OSS4-HMAC-SHA256（V4）签名。
 * 手表端只需要上传，所以这里只保留 PUT 上传的实现。
 */
object OssClient {
    private const val ALGORITHM = "OSS4-HMAC-SHA256"
    private const val PRODUCT = "oss"
    private const val V4_REQUEST = "aliyun_v4_request"
    private const val UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
    private const val HEX = "0123456789abcdef"
    private const val UPPER_HEX = "0123456789ABCDEF"
    private const val CONTENT_TYPE = "audio/mp4"

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(2, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.MINUTES)
        .build()

    class OssException(val code: Int, val detail: String) : Exception("HTTP $code: $detail") {
        val retryable: Boolean
            get() = code == 408 || code == 429 || code >= 500
    }

    fun upload(config: OssConfig, file: File, objectKey: String) {
        val target = buildTarget(config, objectKey)
        val amzDate = utcTimestamp()
        val signedHeaders = TreeMap<String, String>().apply {
            put("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            put("x-oss-date", amzDate)
            put("Content-Type", CONTENT_TYPE)
        }
        val authorization = authorization(config, "PUT", target, signedHeaders, amzDate)
        val request = Request.Builder()
            .url(target.url)
            .header("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            .header("x-oss-date", amzDate)
            .header("Content-Type", CONTENT_TYPE)
            .header("Authorization", authorization)
            .put(file.asRequestBody(CONTENT_TYPE.toMediaType()))
            .build()
        execute(request)
    }

    private data class Target(
        val url: HttpUrl,
        val canonicalUri: String
    )

    private fun buildTarget(config: OssConfig, objectKey: String): Target {
        val requestPath = requestPath(objectKey)
        val url = HttpUrl.Builder()
            .scheme("https")
            .host(config.requestHost)
            .encodedPath(requestPath)
            .build()
        return Target(
            url = url,
            canonicalUri = "/" + config.bucket + requestPath
        )
    }

    private fun requestPath(objectKey: String): String {
        val segments = objectKey.split('/').filter { it.isNotEmpty() }
        if (segments.isEmpty()) return "/"
        return "/" + segments.joinToString("/") { uriEncode(it, ignoreSlashes = false) }
    }

    private fun execute(request: Request): String {
        httpClient.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                throw OssException(response.code, extractError(body, response.message))
            }
            return body
        }
    }

    private fun uriEncode(value: String, ignoreSlashes: Boolean): String {
        val bytes = value.toByteArray(Charsets.UTF_8)
        val out = StringBuilder(bytes.size)
        for (byte in bytes) {
            val code = byte.toInt() and 0xFF
            val char = code.toChar()
            val keep = (char in 'A'..'Z') || (char in 'a'..'z') ||
                (char in '0'..'9') || char == '-' || char == '_' ||
                char == '.' || char == '~' || (ignoreSlashes && char == '/')
            if (keep) {
                out.append(char)
            } else {
                // OSS 的 CanonicalRequest 使用大写百分号编码（如 %2F），签名必须一致。
                out.append('%').append(UPPER_HEX[code ushr 4]).append(UPPER_HEX[code and 0x0F])
            }
        }
        return out.toString()
    }

    private fun authorization(
        config: OssConfig,
        method: String,
        target: Target,
        headers: Map<String, String>,
        amzDate: String
    ): String {
        val region = config.resolvedRegion
        if (region.isEmpty()) {
            throw IllegalArgumentException("无法确定 OSS 地域（Region）")
        }
        val date = amzDate.substring(0, 8)
        val canonicalHeaders = buildCanonicalHeaders(headers)
        val canonicalRequest = buildString {
            append(method).append('\n')
            append(target.canonicalUri).append('\n')
            append('\n') // 没有 query 参数
            // canonicalHeaders 已以换行结尾；再补两个换行对应
            // “CanonicalHeaders + \n + AdditionalHeaders(\"\") + \n + Payload”。
            append(canonicalHeaders).append('\n')
            append('\n')
            append(UNSIGNED_PAYLOAD)
        }

        val scope = "$date/$region/$PRODUCT/$V4_REQUEST"
        val stringToSign = buildString {
            append(ALGORITHM).append('\n')
            append(amzDate).append('\n')
            append(scope).append('\n')
            append(sha256Hex(canonicalRequest.toByteArray(Charsets.UTF_8)))
        }

        var signingKey = hmacSha256(
            ("aliyun_v4" + config.accessKeySecret).toByteArray(Charsets.UTF_8),
            date
        )
        signingKey = hmacSha256(signingKey, region)
        signingKey = hmacSha256(signingKey, PRODUCT)
        signingKey = hmacSha256(signingKey, V4_REQUEST)
        val signature = toHex(hmacSha256(signingKey, stringToSign))

        val credential = "${config.accessKeyId}/$scope"
        return "$ALGORITHM Credential=$credential, Signature=$signature"
    }

    private fun buildCanonicalHeaders(headers: Map<String, String>): String {
        return headers.entries
            .map { it.key.lowercase(Locale.US) to it.value.trim() }
            .filter { (name, _) ->
                name.startsWith("x-oss-") || name == "content-type" || name == "content-md5"
            }
            .sortedBy { it.first }
            .joinToString("") { (name, value) -> "$name:$value\n" }
    }

    private fun utcTimestamp(): String =
        SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(Date())

    private fun extractError(body: String, fallback: String): String {
        if (body.isBlank()) return fallback.ifBlank { "未知错误" }
        val code = Regex("<Code>(.*?)</Code>").find(body)?.groupValues?.getOrNull(1)
        val message = Regex("<Message>(.*?)</Message>").find(body)?.groupValues?.getOrNull(1)
        val combined = listOfNotNull(code, message).joinToString(" - ")
        val value = combined.ifBlank { body }
        return value.replace(Regex("\\s+"), " ").trim().take(400)
    }

    private fun sha256Hex(bytes: ByteArray): String =
        toHex(MessageDigest.getInstance("SHA-256").digest(bytes))

    private fun hmacSha256(key: ByteArray, data: String): ByteArray {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key, "HmacSHA256"))
        return mac.doFinal(data.toByteArray(Charsets.UTF_8))
    }

    private fun toHex(bytes: ByteArray): String {
        val out = CharArray(bytes.size * 2)
        for (i in bytes.indices) {
            val value = bytes[i].toInt() and 0xFF
            out[i * 2] = HEX[value ushr 4]
            out[i * 2 + 1] = HEX[value and 0x0F]
        }
        return String(out)
    }
}

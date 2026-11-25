package com.example.r2recorder.oss

import com.example.r2recorder.data.OssConfig
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import java.io.File
import java.io.IOException
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
 * 只实现本应用需要的上传、下载、列举、删除和存在性检查。
 */
object OssClient {
    private const val ALGORITHM = "OSS4-HMAC-SHA256"
    private const val PRODUCT = "oss"
    private const val V4_REQUEST = "aliyun_v4_request"
    private const val UNSIGNED_PAYLOAD = "UNSIGNED-PAYLOAD"
    private const val LIST_PAGE_SIZE = 1000
    private const val HEX = "0123456789abcdef"
    private const val UPPER_HEX = "0123456789ABCDEF"

    private val httpClient = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(2, TimeUnit.MINUTES)
        .writeTimeout(10, TimeUnit.MINUTES)
        .build()

    data class RemoteObject(
        val key: String,
        val size: Long,
        val lastModifiedMs: Long
    )

    class OssException(val code: Int, val detail: String) : Exception("HTTP $code: $detail") {
        val retryable: Boolean
            get() = code == 408 || code == 429 || code >= 500
    }

    private data class Target(
        val url: HttpUrl,
        val canonicalUri: String,
        val canonicalQuery: String
    )

    fun upload(config: OssConfig, file: File, objectKey: String) {
        val contentType = "audio/mp4"
        val target = buildTarget(config, objectKey, emptyMap())
        val amzDate = utcTimestamp()
        val signedHeaders = TreeMap<String, String>().apply {
            put("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            put("x-oss-date", amzDate)
            put("Content-Type", contentType)
        }
        val authorization = authorization(config, "PUT", target, signedHeaders, amzDate)
        val request = Request.Builder()
            .url(target.url)
            .header("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            .header("x-oss-date", amzDate)
            .header("Content-Type", contentType)
            .header("Authorization", authorization)
            .put(file.asRequestBody(contentType.toMediaType()))
            .build()
        execute(request)
    }

    fun testConnection(config: OssConfig) {
        val target = buildTarget(
            config,
            null,
            linkedMapOf("list-type" to "2", "max-keys" to "1")
        )
        execute(signedRequest(config, "GET", target))
    }

    fun listObjects(config: OssConfig, prefix: String): List<RemoteObject> {
        val result = mutableListOf<RemoteObject>()
        var continuationToken: String? = null
        do {
            val query = linkedMapOf(
                "list-type" to "2",
                "max-keys" to LIST_PAGE_SIZE.toString()
            )
            if (prefix.isNotEmpty()) {
                query["prefix"] = prefix
            }
            continuationToken?.let { query["continuation-token"] = it }
            val body = execute(signedRequest(config, "GET", buildTarget(config, null, query)))
            val (objects, nextToken) = parseListResponse(body)
            result.addAll(objects)
            continuationToken = nextToken
        } while (continuationToken != null)
        return result
    }

    fun download(config: OssConfig, objectKey: String, dest: File) {
        val request = signedRequest(config, "GET", buildTarget(config, objectKey, emptyMap()))
        val parent = dest.absoluteFile.parentFile
        parent?.mkdirs()
        val temp = File(parent, dest.name + ".part")
        try {
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    throw OssException(
                        response.code,
                        extractError(response.body?.string().orEmpty(), response.message)
                    )
                }
                val body = response.body ?: throw IOException("响应内容为空")
                body.byteStream().use { input ->
                    temp.outputStream().use { output -> input.copyTo(output) }
                }
            }
            if (!temp.renameTo(dest)) {
                temp.copyTo(dest, overwrite = true)
                temp.delete()
            }
        } finally {
            if (temp.exists()) {
                temp.delete()
            }
        }
    }

    fun deleteObject(config: OssConfig, objectKey: String) {
        try {
            execute(signedRequest(config, "DELETE", buildTarget(config, objectKey, emptyMap())))
        } catch (e: OssException) {
            if (e.code != 404) throw e
        }
    }

    fun objectExists(config: OssConfig, objectKey: String): Boolean {
        val request = signedRequest(config, "HEAD", buildTarget(config, objectKey, emptyMap()))
        httpClient.newCall(request).execute().use { response ->
            if (response.code == 404) return false
            if (!response.isSuccessful) {
                throw OssException(
                    response.code,
                    extractError(response.body?.string().orEmpty(), response.message)
                )
            }
            return true
        }
    }

    private fun signedRequest(config: OssConfig, method: String, target: Target): Request {
        val amzDate = utcTimestamp()
        val signedHeaders = TreeMap<String, String>().apply {
            put("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            put("x-oss-date", amzDate)
        }
        val authorization = authorization(config, method, target, signedHeaders, amzDate)
        val builder = Request.Builder()
            .url(target.url)
            .header("x-oss-content-sha256", UNSIGNED_PAYLOAD)
            .header("x-oss-date", amzDate)
            .header("Authorization", authorization)
        when (method) {
            "GET" -> builder.get()
            "HEAD" -> builder.head()
            "DELETE" -> builder.delete()
            else -> throw IllegalArgumentException("Unsupported method: $method")
        }
        return builder.build()
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

    private fun buildTarget(
        config: OssConfig,
        objectKey: String?,
        query: Map<String, String>
    ): Target {
        val canonicalQuery = canonicalQuery(query)
        val requestPath = requestPath(objectKey)
        val builder = HttpUrl.Builder()
            .scheme("https")
            .host(config.requestHost)
            .encodedPath(requestPath)
        if (canonicalQuery.isNotEmpty()) {
            builder.encodedQuery(canonicalQuery)
        }
        return Target(
            url = builder.build(),
            canonicalUri = "/" + config.bucket + requestPath,
            canonicalQuery = canonicalQuery
        )
    }

    private fun requestPath(objectKey: String?): String {
        if (objectKey.isNullOrEmpty()) return "/"
        val segments = objectKey.split('/').filter { it.isNotEmpty() }
        if (segments.isEmpty()) return "/"
        return "/" + segments.joinToString("/") { uriEncode(it, ignoreSlashes = false) }
    }

    private fun canonicalQuery(params: Map<String, String>): String =
        params.entries
            .map { uriEncode(it.key, ignoreSlashes = false) to uriEncode(it.value, ignoreSlashes = false) }
            .sortedBy { it.first }
            .joinToString("&") { (name, value) -> if (value.isEmpty()) name else "$name=$value" }

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
            throw IllegalArgumentException("无法确定 OSS 地域（Region），请填写 Region")
        }
        val date = amzDate.substring(0, 8)
        val canonicalHeaders = buildCanonicalHeaders(headers)
        val canonicalRequest = buildString {
            append(method).append('\n')
            append(target.canonicalUri).append('\n')
            append(target.canonicalQuery).append('\n')
            // canonicalHeaders 已以换行结尾；再补两个换行对应
            // “CanonicalHeaders + \\n + AdditionalHeaders(\"\") + \\n + Payload”。
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

    private fun parseListResponse(xml: String): Pair<List<RemoteObject>, String?> {
        val objects = mutableListOf<RemoteObject>()
        val contentsRegex = Regex("<Contents>(.*?)</Contents>", setOf(RegexOption.DOT_MATCHES_ALL))
        for (block in contentsRegex.findAll(xml)) {
            val part = block.groupValues[1]
            val key = Regex("<Key>(.*?)</Key>", setOf(RegexOption.DOT_MATCHES_ALL))
                .find(part)?.groupValues?.get(1) ?: continue
            val size = Regex("<Size>(\\d+)</Size>")
                .find(part)?.groupValues?.get(1)?.toLongOrNull() ?: 0L
            val lastModified = Regex("<LastModified>(.*?)</LastModified>", setOf(RegexOption.DOT_MATCHES_ALL))
                .find(part)?.groupValues?.get(1)
            objects.add(
                RemoteObject(
                    key = unescapeXml(key),
                    size = size,
                    lastModifiedMs = parseIsoMillis(lastModified)
                )
            )
        }
        val truncated = Regex("<IsTruncated>(.*?)</IsTruncated>")
            .find(xml)?.groupValues?.get(1)?.trim()?.equals("true", ignoreCase = true) == true
        val nextToken = if (truncated) {
            Regex("<NextContinuationToken>(.*?)</NextContinuationToken>", setOf(RegexOption.DOT_MATCHES_ALL))
                .find(xml)?.groupValues?.get(1)?.let(::unescapeXml)
        } else {
            null
        }
        return objects to nextToken
    }

    private fun parseIsoMillis(value: String?): Long {
        val text = value?.trim().orEmpty()
        if (text.isEmpty()) return 0L
        val patterns = arrayOf(
            "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'",
            "yyyy-MM-dd'T'HH:mm:ss'Z'"
        )
        for (pattern in patterns) {
            try {
                val format = SimpleDateFormat(pattern, Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("UTC")
                }
                return format.parse(text)?.time ?: 0L
            } catch (_: Exception) {
                // Try the next pattern.
            }
        }
        return 0L
    }

    private fun unescapeXml(value: String): String =
        value.replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", "\"")
            .replace("&apos;", "'")
            .replace("&amp;", "&")

    private fun extractError(body: String, fallback: String): String {
        if (body.isBlank()) return fallback.ifBlank { "Unknown error" }
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

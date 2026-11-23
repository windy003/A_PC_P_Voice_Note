package com.example.r2recorder.r2

import com.example.r2recorder.data.R2Config
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
 * Small S3-compatible client for Cloudflare R2 using AWS Signature Version 4.
 * R2 uses region "auto" and path-style URLs.
 */
object R2Client {
    private const val ALGORITHM = "AWS4-HMAC-SHA256"
    private const val REGION = "auto"
    private const val SERVICE = "s3"
    private const val EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    private const val LIST_PAGE_SIZE = 1000

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

    class R2Exception(val code: Int, val detail: String) : Exception("HTTP $code: $detail") {
        val retryable: Boolean
            get() = code == 408 || code == 429 || code >= 500
    }

    fun upload(config: R2Config, file: File, objectKey: String) {
        val url = buildObjectUrl(config, objectKey)
        val payloadHash = sha256Hex(file)
        val contentType = "audio/mp4"
        executeSigned(config, "PUT", url, payloadHash, contentType) { builder ->
            builder.put(file.asRequestBody(contentType.toMediaType()))
        }
    }

    fun testConnection(config: R2Config) {
        val url = HttpUrl.Builder()
            .scheme("https")
            .host(config.endpointHost)
            .addPathSegment(config.bucket)
            .addQueryParameter("list-type", "2")
            .addQueryParameter("max-keys", "1")
            .build()
        executeSigned(config, "GET", url, EMPTY_SHA256, null)
    }

    fun listObjects(config: R2Config, prefix: String): List<RemoteObject> {
        val result = mutableListOf<RemoteObject>()
        var continuationToken: String? = null
        do {
            val builder = HttpUrl.Builder()
                .scheme("https")
                .host(config.endpointHost)
                .addPathSegment(config.bucket)
                .addQueryParameter("list-type", "2")
                .addQueryParameter("max-keys", LIST_PAGE_SIZE.toString())
            if (prefix.isNotEmpty()) {
                builder.addQueryParameter("prefix", prefix)
            }
            if (continuationToken != null) {
                builder.addQueryParameter("continuation-token", continuationToken)
            }
            val url = builder.build()
            val request = buildSignedRequest(config, "GET", url, EMPTY_SHA256, null)
            httpClient.newCall(request).execute().use { response ->
                val body = response.body?.string().orEmpty()
                if (!response.isSuccessful) {
                    throw R2Exception(response.code, extractError(body, response.message))
                }
                val (objects, nextToken) = parseListResponse(body)
                result.addAll(objects)
                continuationToken = nextToken
            }
        } while (continuationToken != null)
        return result
    }

    fun download(config: R2Config, objectKey: String, dest: File) {
        val url = buildObjectUrl(config, objectKey)
        val request = buildSignedRequest(config, "GET", url, EMPTY_SHA256, null)
        val parent = dest.absoluteFile.parentFile
        parent?.mkdirs()
        val temp = File(parent, dest.name + ".part")
        try {
            httpClient.newCall(request).execute().use { response ->
                if (!response.isSuccessful) {
                    val body = response.body?.string().orEmpty()
                    throw R2Exception(response.code, extractError(body, response.message))
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

    fun deleteObject(config: R2Config, objectKey: String) {
        try {
            executeSigned(
                config,
                "DELETE",
                buildObjectUrl(config, objectKey),
                EMPTY_SHA256,
                null
            )
        } catch (e: R2Exception) {
            if (e.code != 404) throw e
        }
    }

    fun objectExists(config: R2Config, objectKey: String): Boolean {
        val request = buildSignedRequest(
            config,
            "HEAD",
            buildObjectUrl(config, objectKey),
            EMPTY_SHA256,
            null
        )
        httpClient.newCall(request).execute().use { response ->
            if (response.code == 404) return false
            if (!response.isSuccessful) {
                val body = response.body?.string().orEmpty()
                throw R2Exception(response.code, extractError(body, response.message))
            }
            return true
        }
    }

    private fun buildObjectUrl(config: R2Config, objectKey: String): HttpUrl {
        val builder = HttpUrl.Builder()
            .scheme("https")
            .host(config.endpointHost)
            .addPathSegment(config.bucket)
        objectKey.split('/').filter { it.isNotEmpty() }.forEach { builder.addPathSegment(it) }
        return builder.build()
    }

    private fun executeSigned(
        config: R2Config,
        method: String,
        url: HttpUrl,
        payloadHash: String,
        contentType: String?,
        configure: (Request.Builder) -> Unit = {}
    ) {
        val request = buildSignedRequest(
            config, method, url, payloadHash, contentType, configure
        )
        httpClient.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                val body = response.body?.string().orEmpty()
                throw R2Exception(response.code, extractError(body, response.message))
            }
        }
    }

    private fun buildSignedRequest(
        config: R2Config,
        method: String,
        url: HttpUrl,
        payloadHash: String,
        contentType: String?,
        configure: (Request.Builder) -> Unit = {}
    ): Request {
        val now = Date()
        val amzDate = SimpleDateFormat("yyyyMMdd'T'HHmmss'Z'", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(now)
        val dateStamp = SimpleDateFormat("yyyyMMdd", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(now)

        val headers = TreeMap<String, String>()
        if (contentType != null) headers["content-type"] = contentType
        headers["host"] = url.host
        headers["x-amz-content-sha256"] = payloadHash
        headers["x-amz-date"] = amzDate

        val canonicalHeaders = buildString {
            headers.forEach { (name, value) -> append(name).append(':').append(value.trim()).append('\n') }
        }
        val signedHeaders = headers.keys.joinToString(";")
        val canonicalQuery = url.encodedQuery ?: ""
        val canonicalRequest = buildString {
            append(method).append('\n')
            append(url.encodedPath).append('\n')
            append(canonicalQuery).append('\n')
            append(canonicalHeaders).append('\n')
            append(signedHeaders).append('\n')
            append(payloadHash)
        }

        val credentialScope = "$dateStamp/$REGION/$SERVICE/aws4_request"
        val stringToSign = buildString {
            append(ALGORITHM).append('\n')
            append(amzDate).append('\n')
            append(credentialScope).append('\n')
            append(sha256Hex(canonicalRequest.toByteArray(Charsets.UTF_8)))
        }

        var signingKey = hmacSha256(("AWS4" + config.secretAccessKey).toByteArray(Charsets.UTF_8), dateStamp)
        signingKey = hmacSha256(signingKey, REGION)
        signingKey = hmacSha256(signingKey, SERVICE)
        signingKey = hmacSha256(signingKey, "aws4_request")
        val signature = toHex(hmacSha256(signingKey, stringToSign))

        val authorization =
            "$ALGORITHM Credential=${config.accessKeyId}/$credentialScope, " +
                "SignedHeaders=$signedHeaders, Signature=$signature"

        val requestBuilder = Request.Builder()
            .url(url)
            .header("x-amz-date", amzDate)
            .header("x-amz-content-sha256", payloadHash)
            .header("Authorization", authorization)
        if (contentType != null) requestBuilder.header("Content-Type", contentType)
        configure(requestBuilder)
        return requestBuilder.build()
    }

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

    private fun sha256Hex(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buffer = ByteArray(32 * 1024)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                digest.update(buffer, 0, read)
            }
        }
        return toHex(digest.digest())
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
        val digits = "0123456789abcdef"
        for (i in bytes.indices) {
            val value = bytes[i].toInt() and 0xFF
            out[i * 2] = digits[value ushr 4]
            out[i * 2 + 1] = digits[value and 0x0F]
        }
        return String(out)
    }
}

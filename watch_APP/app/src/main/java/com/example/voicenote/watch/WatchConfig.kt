package com.example.voicenote.watch

import com.example.voicenote.watch.data.OssConfig

/** 构建时注入的 OSS 配置。 */
object WatchConfig {
    val oss: OssConfig = OssConfig(
        endpoint = BuildConfig.OSS_ENDPOINT,
        bucket = BuildConfig.OSS_BUCKET,
        accessKeyId = BuildConfig.OSS_ACCESS_KEY_ID,
        accessKeySecret = BuildConfig.OSS_ACCESS_KEY_SECRET,
        region = BuildConfig.OSS_REGION,
        keyPrefix = BuildConfig.OSS_KEY_PREFIX
    )

    val isReady: Boolean
        get() = oss.isComplete
}

package com.example.r2recorder.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 读取/保存用户在「OSS 设置」中手动填写的配置。
 */
class SettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val cipher = SecretCipher()

    fun read(): OssConfig {
        return OssConfig(
            endpoint = prefs.getString(KEY_ENDPOINT, "")?.trim().orEmpty(),
            bucket = prefs.getString(KEY_BUCKET, "")?.trim().orEmpty(),
            accessKeyId = cipher.decrypt(prefs.getString(KEY_ACCESS_KEY, "") ?: "").trim(),
            accessKeySecret = cipher.decrypt(prefs.getString(KEY_SECRET, "") ?: "").trim(),
            region = prefs.getString(KEY_REGION, "")?.trim().orEmpty(),
            publicBaseUrl = prefs.getString(KEY_PUBLIC_URL, "")?.trim().orEmpty(),
            keyPrefix = prefs.getString(KEY_PREFIX, OssConfig.DEFAULT_PREFIX)
                ?.trim()
                .orEmpty()
                .ifEmpty { OssConfig.DEFAULT_PREFIX }
        )
    }

    fun configOrNull(): OssConfig? = read().takeIf { it.isComplete }

    fun save(config: OssConfig) {
        val saved = prefs.edit()
            .putString(KEY_ENDPOINT, config.endpoint.trim())
            .putString(KEY_REGION, config.region.trim())
            .putString(KEY_BUCKET, config.bucket.trim())
            .putString(KEY_ACCESS_KEY, cipher.encrypt(config.accessKeyId.trim()))
            .putString(KEY_SECRET, cipher.encrypt(config.accessKeySecret.trim()))
            .putString(KEY_PUBLIC_URL, config.publicBaseUrl.trim())
            .putString(KEY_PREFIX, config.keyPrefix.trim())
            // 同步写入磁盘，避免保存后马上更新/退出时还没落盘。
            .commit()
        if (!saved) {
            throw IllegalStateException("写入 SharedPreferences 失败")
        }
    }

    private class SecretCipher {
        private val keyStore: KeyStore = KeyStore.getInstance(KEYSTORE_PROVIDER).apply { load(null) }

        @Synchronized
        private fun getKey(): SecretKey {
            val existing = keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
            if (existing != null) return existing.secretKey

            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, KEYSTORE_PROVIDER)
            generator.init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build()
            )
            return generator.generateKey()
        }

        fun encrypt(value: String): String {
            if (value.isEmpty()) return ""
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.ENCRYPT_MODE, getKey())
            val encrypted = cipher.doFinal(value.toByteArray(Charsets.UTF_8))
            return Base64.encodeToString(cipher.iv + encrypted, Base64.NO_WRAP)
        }

        fun decrypt(value: String): String {
            if (value.isEmpty()) return ""
            return try {
                val bytes = Base64.decode(value, Base64.NO_WRAP)
                if (bytes.size <= IV_SIZE) return ""
                val iv = bytes.copyOfRange(0, IV_SIZE)
                val encrypted = bytes.copyOfRange(IV_SIZE, bytes.size)
                val cipher = Cipher.getInstance(TRANSFORMATION)
                cipher.init(Cipher.DECRYPT_MODE, getKey(), GCMParameterSpec(128, iv))
                String(cipher.doFinal(encrypted), Charsets.UTF_8)
            } catch (_: Exception) {
                ""
            }
        }

        companion object {
            private const val KEYSTORE_PROVIDER = "AndroidKeyStore"
            private const val KEY_ALIAS = "oss_recorder_credentials"
            private const val TRANSFORMATION = "AES/GCM/NoPadding"
            private const val IV_SIZE = 12
        }
    }

    companion object {
        private const val PREFS_NAME = "oss_settings"
        private const val KEY_ENDPOINT = "endpoint"
        private const val KEY_REGION = "region"
        private const val KEY_BUCKET = "bucket"
        private const val KEY_ACCESS_KEY = "access_key"
        private const val KEY_SECRET = "secret_key"
        private const val KEY_PUBLIC_URL = "public_url"
        private const val KEY_PREFIX = "key_prefix"
    }
}

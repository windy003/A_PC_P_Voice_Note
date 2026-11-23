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

class SettingsStore(context: Context) {
    private val prefs = context.applicationContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    private val cipher = SecretCipher()

    fun read(): R2Config {
        return R2Config(
            accountId = prefs.getString(KEY_ACCOUNT_ID, "") ?: "",
            bucket = prefs.getString(KEY_BUCKET, "") ?: "",
            accessKeyId = cipher.decrypt(prefs.getString(KEY_ACCESS_KEY, "") ?: ""),
            secretAccessKey = cipher.decrypt(prefs.getString(KEY_SECRET, "") ?: ""),
            publicBaseUrl = prefs.getString(KEY_PUBLIC_URL, "") ?: "",
            keyPrefix = prefs.getString(KEY_PREFIX, R2Config.DEFAULT_PREFIX) ?: R2Config.DEFAULT_PREFIX
        )
    }

    fun configOrNull(): R2Config? {
        val config = read()
        return if (config.accountId.isNotBlank() &&
            config.bucket.isNotBlank() &&
            config.accessKeyId.isNotBlank() &&
            config.secretAccessKey.isNotBlank()
        ) {
            config
        } else {
            null
        }
    }

    fun save(config: R2Config) {
        val saved = prefs.edit()
            .putString(KEY_ACCOUNT_ID, config.accountId.trim())
            .putString(KEY_BUCKET, config.bucket.trim())
            .putString(KEY_ACCESS_KEY, cipher.encrypt(config.accessKeyId.trim()))
            .putString(KEY_SECRET, cipher.encrypt(config.secretAccessKey.trim()))
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
            private const val KEY_ALIAS = "r2_recorder_credentials"
            private const val TRANSFORMATION = "AES/GCM/NoPadding"
            private const val IV_SIZE = 12
        }
    }

    companion object {
        private const val PREFS_NAME = "r2_settings"
        private const val KEY_ACCOUNT_ID = "account_id"
        private const val KEY_BUCKET = "bucket"
        private const val KEY_ACCESS_KEY = "access_key"
        private const val KEY_SECRET = "secret_key"
        private const val KEY_PUBLIC_URL = "public_url"
        private const val KEY_PREFIX = "key_prefix"
    }
}

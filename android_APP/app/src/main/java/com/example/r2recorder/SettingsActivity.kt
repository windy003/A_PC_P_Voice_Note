package com.example.r2recorder

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.isVisible
import androidx.lifecycle.lifecycleScope
import com.example.r2recorder.data.R2Config
import com.example.r2recorder.data.SettingsStore
import com.example.r2recorder.databinding.ActivitySettingsBinding
import com.example.r2recorder.r2.R2Client
import com.example.r2recorder.util.Toasts
import com.google.android.material.snackbar.Snackbar
import com.google.android.material.textfield.TextInputLayout
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding
    private lateinit var settingsStore: SettingsStore

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        settingsStore = SettingsStore(this)
        binding.toolbar.setNavigationOnClickListener { finish() }

        val config = settingsStore.read()
        binding.etAccountId.setText(config.accountId)
        binding.etBucket.setText(config.bucket)
        binding.etAccessKey.setText(config.accessKeyId)
        binding.etSecretKey.setText(config.secretAccessKey)
        binding.etPublicUrl.setText(config.publicBaseUrl)
        binding.etPrefix.setText(config.keyPrefix.ifBlank { R2Config.DEFAULT_PREFIX })

        binding.btnSave.setOnClickListener {
            val form = readForm() ?: return@setOnClickListener
            try {
                settingsStore.save(form)
                Toasts.show(this, getString(R.string.config_saved))
                finish()
            } catch (e: Exception) {
                showMessage(getString(R.string.unknown_error) + ": " + (e.message ?: ""))
            }
        }

        binding.btnTest.setOnClickListener {
            val form = readForm() ?: return@setOnClickListener
            testConnection(form)
        }
    }

    private fun readForm(): R2Config? {
        clearErrors()

        val accountId = binding.etAccountId.text?.toString().orEmpty().trim()
        val bucket = binding.etBucket.text?.toString().orEmpty().trim()
        val accessKey = binding.etAccessKey.text?.toString().orEmpty().trim()
        val secretKey = binding.etSecretKey.text?.toString().orEmpty().trim()
        val publicUrl = binding.etPublicUrl.text?.toString().orEmpty().trim()
        val prefix = binding.etPrefix.text?.toString().orEmpty().trim()

        var valid = true
        if (accountId.isEmpty()) {
            binding.layoutAccountId.error = getString(R.string.error_required)
            valid = false
        }
        if (bucket.isEmpty()) {
            binding.layoutBucket.error = getString(R.string.error_required)
            valid = false
        }
        if (accessKey.isEmpty()) {
            binding.layoutAccessKey.error = getString(R.string.error_required)
            valid = false
        }
        if (secretKey.isEmpty()) {
            binding.layoutSecretKey.error = getString(R.string.error_required)
            valid = false
        }
        if (publicUrl.isNotEmpty() &&
            !publicUrl.startsWith("https://") &&
            !publicUrl.startsWith("http://")
        ) {
            binding.layoutPublicUrl.error = getString(R.string.error_url_invalid)
            valid = false
        }
        if (!valid) return null

        return R2Config(
            accountId = accountId,
            bucket = bucket,
            accessKeyId = accessKey,
            secretAccessKey = secretKey,
            publicBaseUrl = publicUrl,
            keyPrefix = prefix.ifBlank { R2Config.DEFAULT_PREFIX }
        )
    }

    private fun clearErrors() {
        binding.layoutAccountId.error = null
        binding.layoutBucket.error = null
        binding.layoutAccessKey.error = null
        binding.layoutSecretKey.error = null
        binding.layoutPublicUrl.error = null
        binding.layoutPrefix.error = null
    }

    private fun testConnection(config: R2Config) {
        setBusy(true)
        lifecycleScope.launch {
            val unknown = getString(R.string.unknown_error)
            val error = withContext(Dispatchers.IO) {
                try {
                    R2Client.testConnection(config)
                    null
                } catch (e: Exception) {
                    e.message ?: unknown
                }
            }
            setBusy(false)
            if (error == null) {
                Snackbar.make(
                    binding.root,
                    Toasts.withTotalCount(this@SettingsActivity, getString(R.string.settings_test_ok)),
                    Snackbar.LENGTH_LONG
                ).show()
            } else {
                showMessage(getString(R.string.settings_test_failed, error))
            }
        }
    }

    private fun setBusy(busy: Boolean) {
        binding.btnSave.isEnabled = !busy
        binding.btnTest.isEnabled = !busy
        binding.progress.isVisible = busy
    }

    private fun showMessage(message: String) {
        Snackbar.make(
            binding.root,
            Toasts.withTotalCount(this, message),
            Snackbar.LENGTH_LONG
        ).show()
    }
}

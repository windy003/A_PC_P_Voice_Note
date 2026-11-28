import java.io.File

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

/**
 * 读取 watch_APP/.env 中的 key=value 配置。
 * 忽略注释与空行，支持成对的单/双引号。
 */
fun readKeyValueFile(file: File): Map<String, String> {
    if (!file.isFile) return emptyMap()
    val result = linkedMapOf<String, String>()
    file.readLines(Charsets.UTF_8).forEachIndexed { index, raw ->
        var line = if (index == 0) raw.removePrefix("﻿") else raw
        line = line.trim()
        if (line.isEmpty() || line.startsWith("#")) return@forEachIndexed
        val separator = line.indexOf('=')
        if (separator <= 0) return@forEachIndexed
        val key = line.substring(0, separator).trim()
        if (!key.matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))) return@forEachIndexed
        var value = line.substring(separator + 1).trim()
        if (value.length >= 2 && value.first() == value.last() && value.first() in "\"'") {
            val quote = value.first()
            value = value.substring(1, value.length - 1)
            if (quote == '"') {
                value = value
                    .replace("\\n", "\n")
                    .replace("\\t", "\t")
                    .replace("\\\"", "\"")
                    .replace("\\\\", "\\")
            }
        } else {
            value = value.substringBefore(" #").trim()
        }
        result[key] = value
    }
    return result
}

fun String.forBuildConfig(): String {
    val escaped = replace("\\", "\\\\")
        .replace("\"", "\\\"")
        .replace("\r", "")
        .replace("\n", "\\n")
    return "\"$escaped\""
}

val projectDirRoot = rootProject.projectDir
val watchEnvFile = projectDirRoot.resolve(".env")
val envValues = readKeyValueFile(watchEnvFile)

fun configValue(key: String): String =
    System.getenv(key)?.takeIf { it.isNotBlank() } ?: envValues[key].orEmpty()

val requiredOssKeys = listOf(
    "OSS_ENDPOINT",
    "OSS_BUCKET",
    "OSS_ACCESS_KEY_ID",
    "OSS_ACCESS_KEY_SECRET"
)
val ossConfigured = requiredOssKeys.all { configValue(it).isNotBlank() }
logger.lifecycle(
    if (ossConfigured) {
        "watch: 已从 ${watchEnvFile.absolutePath} 注入 OSS 配置"
    } else {
        "watch: ${watchEnvFile.absolutePath} 中缺少完整的 OSS 配置，应用将无法上传。" +
            "请填写 " + requiredOssKeys.joinToString(" / ")
    }
)

android {
    namespace = "com.example.voicenote.watch"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.voicenote.watch"
        minSdk = 26
        targetSdk = 34
        versionCode = 1
        versionName = "2026/11/28-1"

        buildConfigField("String", "OSS_ENDPOINT", configValue("OSS_ENDPOINT").forBuildConfig())
        buildConfigField("String", "OSS_REGION", configValue("OSS_REGION").forBuildConfig())
        buildConfigField("String", "OSS_BUCKET", configValue("OSS_BUCKET").forBuildConfig())
        buildConfigField("String", "OSS_ACCESS_KEY_ID", configValue("OSS_ACCESS_KEY_ID").forBuildConfig())
        buildConfigField("String", "OSS_ACCESS_KEY_SECRET", configValue("OSS_ACCESS_KEY_SECRET").forBuildConfig())
        buildConfigField("String", "OSS_KEY_PREFIX", configValue("OSS_KEY_PREFIX").forBuildConfig())
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        buildConfig = true
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-ktx:1.8.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}

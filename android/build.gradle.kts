plugins {
    // AGP 9 compiles Kotlin itself, so there's no separate Kotlin plugin.
    // 9.4 is the first AGP line that builds for Android 17 (API 37).
    id("com.android.application") version "9.4.0" apply false
    // Chaquopy embeds Python, so the same server code runs on the phone.
    id("com.chaquo.python") version "17.0.0" apply false
}

plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.1.0" apply false
    // Chaquopy embeds Python, so the same server code runs on the phone.
    id("com.chaquo.python") version "17.0.0" apply false
}

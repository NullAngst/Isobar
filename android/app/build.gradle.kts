import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("com.chaquo.python")
}

// The repo root, one level above android/.
val repoRoot: File = rootProject.projectDir.parentFile

// Version comes from isobar/__init__.py, so desktop and Android always match.
// 1.2.3 becomes versionCode 10203, which keeps rising with every release.
val isobarVersion: String = Regex("""__version__\s*=\s*"([^"]+)"""")
    .find(repoRoot.resolve("isobar/__init__.py").readText())
    ?.groupValues?.get(1) ?: "0.0.0"
val isobarVersionCode: Int = isobarVersion.split(".")
    .map { part -> part.takeWhile { it.isDigit() }.toIntOrNull() ?: 0 }
    .plus(listOf(0, 0, 0))
    .let { it[0] * 10000 + it[1] * 100 + it[2] }

// Signing details come from environment variables, which the GitHub workflow
// fills from repository secrets. Without them the release APK is left unsigned.
val keystorePath: String? = System.getenv("ISOBAR_KEYSTORE")?.takeIf { it.isNotBlank() }

android {
    namespace = "io.github.nullangst.isobar"
    // Android 17. Targeting it also turns off the implicit "local network"
    // grant Android 17 gives older apps, which shows up as "Nearby devices".
    // Isobar only talks to its own server on 127.0.0.1 (which is not the local
    // network) and to the internet, so it needs no permission beyond INTERNET.
    compileSdk = 37

    defaultConfig {
        applicationId = "io.github.nullangst.isobar"
        minSdk = 24
        targetSdk = 37
        versionCode = isobarVersionCode
        versionName = isobarVersion

        ndk {
            // Python 3.12 only ships 64-bit builds. arm64 covers phones and
            // tablets from about 2017 on; x86_64 covers emulators and Chromebooks.
            abiFilters += listOf("arm64-v8a", "x86_64")
        }
    }

    signingConfigs {
        if (keystorePath != null) {
            create("release") {
                storeFile = file(keystorePath)
                storePassword = System.getenv("ISOBAR_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("ISOBAR_KEY_ALIAS")
                keyPassword = System.getenv("ISOBAR_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (keystorePath != null) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    compilerOptions {
        jvmTarget = JvmTarget.JVM_17
    }
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.core:core-ktx:1.16.0")
}

// Copy the isobar package (server, data sources and web UI) out of the repo
// root into the build folder, and hand that to Chaquopy as Python source.
val syncIsobar by tasks.registering(Sync::class) {
    from(repoRoot.resolve("isobar")) {
        exclude("**/__pycache__/**", "**/*.pyc")
    }
    into(layout.buildDirectory.dir("isobar-python/isobar"))
}
tasks.named("preBuild") { dependsOn(syncIsobar) }
tasks.configureEach {
    if (name != "syncIsobar" && name.contains("Python")) dependsOn(syncIsobar)
}

chaquopy {
    defaultConfig {
        // The build machine needs this same Python version (3.12.x) on its PATH.
        version = "3.12"
        pip {
            install("requests")
        }
    }
    sourceSets {
        getByName("main") {
            srcDir("build/isobar-python")
        }
    }
}

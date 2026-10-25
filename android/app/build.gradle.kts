plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

fun releaseSetting(name: String): String? =
    (project.findProperty(name) as String?) ?: System.getenv(name)

// Fail before any work starts when a release artifact is requested without a
// release key. Checked on the task graph, so debug builds and tests are unaffected.
gradle.taskGraph.whenReady {
    val wantsRelease = allTasks.any {
        it.project == project && it.name in setOf("packageRelease", "signReleaseBundle")
    }
    if (wantsRelease && releaseSetting("IOFAST_STORE_FILE") == null &&
        releaseSetting("IOFAST_SIGN_WITH_DEBUG") != "1"
    ) {
        throw GradleException(
            "Release signing key not configured: set IOFAST_STORE_FILE, IOFAST_STORE_PASSWORD, " +
                "IOFAST_KEY_ALIAS and IOFAST_KEY_PASSWORD (gradle property or environment " +
                "variable). To sign with the debug keystore on purpose, set IOFAST_SIGN_WITH_DEBUG=1. " +
                "See docs/RELEASING.md."
        )
    }
}

android {
    namespace = "com.iofast.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.iofast.android"
        minSdk = 30
        targetSdk = 34
        // versionCode is what phones compare when self-updating (UpdateWorker):
        // raise it by hand on every release. versionName comes from VERSION.
        versionCode = 9
        versionName = rootProject.file("../VERSION").readText().trim()

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Release signing needs IOFAST_STORE_FILE, IOFAST_STORE_PASSWORD,
    // IOFAST_KEY_ALIAS and IOFAST_KEY_PASSWORD (gradle property or env var).
    // Unset: a release build stops with an error (taskGraph check at the top
    // of this file) instead of silently signing with the debug keystore.
    // Phones running a debug-key build only update in place from another
    // debug-key build: to do that on purpose, set IOFAST_SIGN_WITH_DEBUG=1. Debug builds and unit
    // tests never need any of this.
    signingConfigs {
        create("release") {
            val store = releaseSetting("IOFAST_STORE_FILE")
            if (store != null) {
                storeFile = file(store)
                storePassword = releaseSetting("IOFAST_STORE_PASSWORD")
                keyAlias = releaseSetting("IOFAST_KEY_ALIAS")
                keyPassword = releaseSetting("IOFAST_KEY_PASSWORD")
            } else if (releaseSetting("IOFAST_SIGN_WITH_DEBUG") == "1") {
                storeFile = file(System.getProperty("user.home") + "/.android/debug.keystore")
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
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
        viewBinding = true
    }

    testOptions {
        unitTests {
            isIncludeAndroidResources = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.7.0")
    implementation("androidx.recyclerview:recyclerview:1.3.2")

    // Background sends to the PC inbox
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // Room
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    // Testing
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.robolectric:robolectric:4.11.1")
    testImplementation("androidx.test:core:1.5.0")
    testImplementation("androidx.test.ext:junit:1.1.5")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.7.3")
}
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.iofast.android"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.iofast.android"
        minSdk = 30
        targetSdk = 34
        versionCode = 7
        versionName = "0.2.4"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Release signing: a real key when IOFAST_STORE_FILE etc. are set (gradle
    // property or env var); otherwise the machine's debug keystore, which is
    // the key 0.1.x was signed with, so 0.2.0 installs over it in place.
    signingConfigs {
        create("release") {
            fun setting(name: String): String? =
                (project.findProperty(name) as String?) ?: System.getenv(name)
            val store = setting("IOFAST_STORE_FILE")
            if (store != null) {
                storeFile = file(store)
                storePassword = setting("IOFAST_STORE_PASSWORD")
                keyAlias = setting("IOFAST_KEY_ALIAS")
                keyPassword = setting("IOFAST_KEY_PASSWORD")
            } else {
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
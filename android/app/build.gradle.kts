plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "net.prototux.lapin"
    compileSdk {
        version = release(37)
    }

    defaultConfig {
        applicationId = "net.prototux.lapin"
        minSdk = 26
        targetSdk = 37
        versionCode = 1
        versionName = "1.0.0"
    }

    signingConfigs {
        // Release key from the environment (the release workflow's secrets).
        // Without it the release APK is unsigned: use the debug one locally.
        val keystore = providers.environmentVariable("LAPIN_KEYSTORE").orNull
        if (keystore != null) {
            create("release") {
                storeFile = file(keystore)
                storePassword = providers.environmentVariable("LAPIN_KEYSTORE_PASSWORD").orNull
                keyAlias = providers.environmentVariable("LAPIN_KEY_ALIAS").orNull
                keyPassword = providers.environmentVariable("LAPIN_KEY_PASSWORD").orNull
            }
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.findByName("release")
            optimization {
                enable = false
            }
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    testOptions {
        unitTests.isReturnDefaultValues = true
    }
    lint {
        // a sideloaded household app: no Play Store policy checks
        disable += setOf("ProtectedPermissions", "QueryAllPackagesPermission")
        // versions are pinned to what's in the local Gradle cache; bump them deliberately
        disable += setOf("GradleDependency", "NewerVersionAvailable", "AndroidGradlePluginVersion")
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.compose.material3)
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.ui.graphics)
    implementation(libs.androidx.compose.ui.tooling.preview)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.viewmodel)
    implementation(libs.okhttp)
    implementation(libs.androidx.car.app)
    implementation(libs.androidx.car.app.projected)
    implementation(libs.media3.session)
    testImplementation(libs.junit)
    testImplementation(libs.org.json)   // real org.json for JVM tests (android.jar only has stubs)
    debugImplementation(libs.androidx.compose.ui.tooling)
}

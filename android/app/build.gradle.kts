import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Sürüm numarası Windows sürümüyle ortak: instagram_indirici/__init__.py
val repoRoot = rootProject.file("..")
val appVersion: String = Regex("__version__\\s*=\\s*\"([^\"]+)\"")
    .find(File(repoRoot, "instagram_indirici/__init__.py").readText())?.groupValues?.get(1) ?: "0.0.0"
val buildNumber = System.getenv("GITHUB_RUN_NUMBER")?.toIntOrNull() ?: 1

// Arayüz (index.html) ve çeviri dosyaları Windows sürümüyle ortak; derlemede uygulamaya kopyalanır.
val sharedAssetsDir = layout.buildDirectory.dir("generated/sharedAssets")
val copySharedAssets by tasks.registering(Sync::class) {
    from(File(repoRoot, "instagram_indirici/ui")) { include("index.html") }
    from(File(repoRoot, "ceviriler")) { into("ceviriler") }
    into(sharedAssetsDir)
}

// Uygulamanın telefondaki adı da çeviri dosyalarından (ui.app_title) gelir:
// en.json -> values/strings.xml, tr.json -> values-tr/strings.xml ...
val sharedResDir = layout.buildDirectory.dir("generated/sharedRes")
val generateAppName by tasks.registering {
    val src = File(repoRoot, "ceviriler")
    val out = sharedResDir.get().asFile
    inputs.dir(src)
    outputs.dir(out)
    doLast {
        out.deleteRecursively()
        fun esc(s: String) = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "\\'").replace("\"", "\\\"")
        src.listFiles { f -> f.name.endsWith(".json") }!!.sortedBy { it.name }.forEach { f ->
            @Suppress("UNCHECKED_CAST")
            val data = groovy.json.JsonSlurper().parse(f, "UTF-8") as Map<String, Any?>
            val title = data["ui.app_title"] as? String ?: return@forEach
            val code = f.name.removeSuffix(".json")
            val dir = File(out, if (code == "en") "values" else "values-$code")
            dir.mkdirs()
            File(dir, "strings.xml").writeText(
                "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<resources>\n" +
                    "    <string name=\"app_name\">${esc(title)}</string>\n</resources>\n",
                Charsets.UTF_8,
            )
        }
    }
}

android {
    namespace = "io.github.avsessisozx.instagramindirici"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.avsessisozx.instagramindirici"
        minSdk = 29
        targetSdk = 35
        versionCode = buildNumber
        versionName = appVersion
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    // Her derleme depodaki aynı anahtarla imzalanır; yeni sürüm eskisinin üzerine kurulabilir.
    // Bu anahtar yalnızca kişisel kullanım içindir.
    signingConfigs {
        create("shared") {
            storeFile = rootProject.file("keystore/instagram-indirici.jks")
            storePassword = "instagramindirici"
            keyAlias = "instagramindirici"
            keyPassword = "instagramindirici"
        }
    }

    buildTypes {
        debug {
            signingConfig = signingConfigs.getByName("shared")
        }
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("shared")
        }
    }

    sourceSets["main"].assets.srcDir(sharedAssetsDir)
    sourceSets["main"].res.srcDir(sharedResDir)
    // Ortak test verileri cihazda da denensin (Android'in kendi düzenli ifade ve JSON motoruyla).
    sourceSets["androidTest"].assets.srcDir(File(repoRoot, "tests/vectors"))

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
        unitTests.all {
            it.systemProperty("vectorsDir", File(repoRoot, "tests/vectors").absolutePath)
            it.systemProperty("translationsDir", File(repoRoot, "ceviriler").absolutePath)
            it.systemProperty("fakeBase", System.getenv("FAKE_INSTAGRAM") ?: "")
            it.jvmArgs("-Duser.timezone=UTC")
            it.testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
        }
    }

    lint {
        abortOnError = false
    }
}

tasks.named("preBuild") { dependsOn(copySharedAssets, generateAppName) }

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.json:json:20240303")

    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:rules:1.6.1")
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
}

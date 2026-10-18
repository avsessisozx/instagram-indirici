package io.github.avsessisozx.instagramindirici

import android.app.Application
import android.content.Context
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import java.io.File
import java.util.Locale

class App : Application() {
    override fun onCreate() {
        super.onCreate()
        Core.init(this)
    }
}

/** Uygulama genelinde tek kopya: çeviriler, ayarlar, geçmiş, indirme motoru. */
object Core {
    lateinit var app: Context; private set
    lateinit var tr: Tr; private set
    lateinit var engine: Engine; private set
    @Volatile var web: IgWeb? = null
    @Volatile var settings: SettingsValues = SettingsValues()
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private const val PREFS = "ayarlar"
    private var historyInstance: History? = null

    @Synchronized
    fun init(ctx: Context) {
        if (::engine.isInitialized) return
        app = ctx.applicationContext
        val langs = (app.assets.list("ceviriler") ?: emptyArray())
            .filter { it.endsWith(".json") }.map { it.removeSuffix(".json") }.sorted()
            .ifEmpty { listOf(Tr.FALLBACK) }
        tr = Tr({ code ->
            try { app.assets.open("ceviriler/$code.json").bufferedReader(Charsets.UTF_8).use { it.readText() } } catch (e: Exception) { null }
        }, langs)
        settings = SettingsValues.fromJson(prefs().getString("json", null), langs)
        engine = Engine(
            webProvider = { web },
            sinkFactory = { TestHooks.sinkFactory?.invoke() ?: MediaStoreSink(app) },
            historyProvider = { history() },
            scope = scope,
            onLoginState = { saveLoginState(it) },
        )
    }

    private fun prefs() = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    @Synchronized
    fun history(): History {
        val file = TestHooks.historyFile ?: File(app.filesDir, "gecmis.json")
        val h = historyInstance
        if (h != null && h.fileMatches(file)) return h
        return History(file).also { historyInstance = it }
    }

    fun languages(): List<String> = tr.available().map { it.first }

    fun saveSettings(v: SettingsValues) {
        settings = v
        prefs().edit().putString("json", v.toJson().toString()).apply()
    }

    /** Son bilinen Instagram giriş durumu (arayüzün altındaki yeşil nokta). */
    fun loginState(): Boolean? = if (prefs().contains("giris")) prefs().getBoolean("giris", false) else null

    fun saveLoginState(v: Boolean) { prefs().edit().putBoolean("giris", v).apply() }

    fun lang(): String = tr.resolve(settings.lang, Locale.getDefault().language)

    fun text(key: String, vararg params: Pair<String, Any?>): String = tr.text(key, lang(), mapOf(*params))

    fun version(): String = try {
        app.packageManager.getPackageInfo(app.packageName, 0).versionName ?: ""
    } catch (e: Exception) { "" }
}

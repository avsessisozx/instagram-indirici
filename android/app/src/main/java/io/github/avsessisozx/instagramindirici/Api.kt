package io.github.avsessisozx.instagramindirici

import android.content.ClipboardManager
import android.content.Context
import android.webkit.JavascriptInterface
import org.json.JSONArray
import org.json.JSONObject

/** Arayüzün (index.html) çağırdığı istekler. Windows sürümündeki server.py'nin karşılığı. */
interface ApiHost {
    fun startDownloadService()
    fun openResult(location: String?)
    fun requestNotificationPermission()
    fun showVerification()
}

class Api(private val host: ApiHost) {

    /** index.html bu nesneyi window.InstagramApp olarak görür. */
    @JavascriptInterface
    fun call(path: String, body: String): String {
        val (status, out) = try {
            handle(path, if (body.isBlank()) null else JSONObject(body))
        } catch (e: Exception) {
            400 to JSONObject().put("error", msg("server.bad_request").toJson())
        }
        return JSONObject().put("status", status).put("body", out).toString()
    }

    private fun err(code: Int, m: Msg) = code to JSONObject().put("error", m.toJson())

    fun handle(fullPath: String, data: JSONObject?): Pair<Int, JSONObject> {
        val path = fullPath.substringBefore('?')
        val query = fullPath.substringAfter('?', "")
        val engine = Core.engine
        return when (path) {
            "/api/ping" -> 200 to JSONObject().put("app", "instagram-indirici").put("version", Core.version())

            "/api/strings" -> {
                val lang = Core.lang()
                val langs = JSONArray()
                for ((code, name) in Core.tr.available()) langs.put(JSONObject().put("code", code).put("name", name))
                200 to JSONObject()
                    .put("lang", lang)
                    .put("setting", Core.settings.lang)
                    .put("languages", langs)
                    .put("platform", "mobile")
                    .put("strings", JSONObject(Core.tr.merged(lang) as Map<*, *>))
            }

            "/api/state" -> {
                val since = Regex("(?:^|&)since=(\\d+)").find(query)?.groupValues?.get(1)?.toIntOrNull() ?: 0
                200 to engine.snapshot(since)
                    .put("settings", Core.settings.toJson())
                    .put("version", Core.version())
                    .put("app_name", Core.text("ui.app_title"))
            }

            "/api/parse" -> {
                val p = Links.parse(data?.optString("link", "") ?: "")
                200 to JSONObject().put("ok", p.ok).put("kind", p.kind ?: JSONObject.NULL)
                    .put("message", p.describe().toJson()).put("username", p.username)
            }

            "/api/settings" -> {
                Core.saveSettings(Core.settings.merge(data, Core.languages()))
                200 to JSONObject().put("settings", Core.settings.toJson())
            }

            "/api/start" -> {
                val mode = data?.optString("mode", "") ?: ""
                if (mode != Engine.MODE_SINGLE && mode != Engine.MODE_PROFILE) return err(400, msg("server.choose_mode"))
                Core.saveSettings(Core.settings.merge(data?.optJSONObject("settings"), Core.languages()))
                val e = engine.start(Engine.Job(data?.optString("link", "") ?: "", mode, Core.settings))
                if (e != null) return err(400, e)
                host.requestNotificationPermission()
                host.startDownloadService()
                200 to JSONObject().put("ok", true)
            }

            "/api/login" -> {
                val e = engine.start(Engine.Job("", Engine.MODE_LOGIN, Core.settings))
                if (e != null) return err(400, e)
                200 to JSONObject().put("ok", true)
            }

            "/api/clear-history" -> {
                if (engine.running) return err(400, msg("engine.already_running"))
                val n = try { Core.history().clear() } catch (e: Exception) {
                    return err(500, msg("engine.history_failed", "error" to (e.message ?: e.javaClass.simpleName)))
                }
                200 to JSONObject().put("ok", true).put("message", msg("server.history_cleared", "n" to n).toJson())
            }

            "/api/stop" -> {
                engine.stop()
                200 to JSONObject().put("ok", true)
            }

            "/api/clipboard" -> {
                val cm = Core.app.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val text = try { cm.primaryClip?.getItemAt(0)?.coerceToText(Core.app)?.toString() } catch (e: Exception) { null }
                200 to JSONObject().put("text", text ?: "")
            }

            "/api/open-folder" -> {
                host.openResult(engine.lastLocation)
                200 to JSONObject().put("ok", true)
            }

            "/api/show-verify" -> {
                host.showVerification()
                200 to JSONObject().put("ok", true)
            }

            "/api/choose-folder" -> err(400, msg("server.no_folder_dialog"))

            else -> err(404, msg("server.unknown"))
        }
    }
}

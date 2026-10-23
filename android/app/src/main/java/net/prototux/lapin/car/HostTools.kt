package net.prototux.lapin.car

import android.content.Context
import android.net.Uri
import android.os.Bundle
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject

/**
 * Car tools offered by the car host when Lapin runs on AARE (an open-source Android Auto): show
 * the car's status, navigate in the car's navigation app, control the music, open car apps,
 * read the latest messages… AARE publishes their specs and runs them; Lapin passes the specs to
 * the server with its own device tools and forwards the calls. Tool names start with "car_".
 *
 * AARE only answers the app chosen as the car's voice assistant, and only while a car (or its
 * on-phone preview) is connected: elsewhere there are no car tools.
 */
object HostTools {
    private const val TAG = "LapinHostTools"
    private val URI = Uri.parse("content://net.prototux.aare.car")

    fun handles(name: String) = name.startsWith("car_")

    /** The car's tool specs, or none when no car host answers. */
    fun specs(context: Context): List<JSONObject> = try {
        val json = context.contentResolver.call(URI, "tools", null, null)?.getString("json") ?: "[]"
        val a = JSONArray(json)
        (0 until a.length()).map { a.getJSONObject(it) }.filter { handles(it.optString("name")) }
    } catch (e: Exception) {
        // No AARE, not the chosen assistant, or no car: no car tools.
        Log.i(TAG, "no car tools: ${e.message}")
        emptyList()
    }

    /** Runs a car tool in the car host. Blocking: call off the main thread. */
    fun run(context: Context, name: String, args: JSONObject): JSONObject = try {
        val extras = Bundle().apply { putString("args", args.toString()) }
        val result = context.contentResolver.call(URI, "run", name, extras)?.getString("result")
        if (result != null) JSONObject(result) else JSONObject().put("error", "the car didn't answer")
    } catch (e: Exception) {
        JSONObject().put("error", "the car is not connected (${e.message})")
    }
}

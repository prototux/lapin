package net.prototux.lapin.tools

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.location.Geocoder
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.BatteryManager
import android.os.Build
import android.os.CancellationSignal
import android.os.SystemClock
import android.provider.AlarmClock
import android.provider.CalendarContract
import android.telephony.SmsManager
import androidx.core.net.toUri
import net.prototux.lapin.core.Assistant
import net.prototux.lapin.music.MusicPlayer
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Runs the device tools (PROTOCOL.md §5). Results are short, factual, in English. Called off the main thread. */
class DeviceTools(private val context: Context, private val assistant: Assistant) {
    private val contacts = Contacts(context)
    private val apps = Apps(context)
    private val music = MusicPlayer(context, { assistant.prefs.musicApp }, { assistant.launch(it) })

    fun run(name: String, a: JSONObject): JSONObject = when (name) {
        "send_phone_message", "send_message" -> sendMessage(a.str("contact"), a.str("message"), a.str("app").ifEmpty { "sms" })
        "call_contact" -> call(a.str("contact"))
        "find_contact" -> findContact(a.str("name"))
        "start_navigation" -> navigate(a.str("destination"), a.str("mode").ifEmpty { "driving" })
        "open_app" -> openApp(a.str("app"))
        "set_phone_alarm" -> alarm(a.optInt("hour", -1), a.optInt("minute", 0), a.str("label"))
        "set_phone_timer" -> timer(a.optInt("seconds", 0), a.str("label"))
        "flashlight" -> flashlight(a.optBoolean("on", true))
        "phone_play_music" -> playMusic(a.str("query"), a.str("kind").ifEmpty { "auto" })
        "phone_media", "tv_media" -> music.control(a.str("action"))
        "phone_now_playing" -> music.nowPlaying()
        "get_location" -> location()
        "battery_status" -> battery()
        "open_url" -> openUrl(a.str("url"))
        "create_calendar_event" -> calendar(a.str("title"), a.str("start_iso"), a.str("end_iso"), a.str("location"))
        "do_not_disturb" -> dnd(a.optBoolean("on", true))
        else -> err("unknown tool $name")
    }

    // ------------------------------------------------------------------ helpers
    private fun JSONObject.str(k: String) = if (isNull(k)) "" else optString(k, "").trim()
    private fun err(msg: String) = JSONObject().put("error", msg)
    private fun ok() = JSONObject().put("ok", true)
    private fun granted(p: String) = context.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    private fun noPermission(what: String) =
        err("permission to $what not granted; ask the user to open the Lapin app on the phone and allow it")

    private fun resolveContact(contact: String): Pair<Contacts.Resolved.Ok?, JSONObject?> =
        when (val r = contacts.resolve(contact)) {
            is Contacts.Resolved.Ok -> r to null
            is Contacts.Resolved.Err -> null to err(r.error).apply {
                if (r.candidates.isNotEmpty()) put("candidates", JSONArray(r.candidates))
            }
        }

    private fun installed(pkg: String) = try { context.packageManager.getPackageInfo(pkg, 0); true } catch (_: PackageManager.NameNotFoundException) { false }

    // ------------------------------------------------------------------ messages and calls
    private fun sendMessage(contact: String, message: String, app: String): JSONObject {
        if (message.isEmpty()) return err("empty message")
        val (who, e) = resolveContact(contact)
        if (who == null) return e!!
        return if (app.lowercase() == "signal") {
            if (!installed(SIGNAL)) return err("Signal is not installed on the phone")
            val sendTo = Intent(Intent.ACTION_SENDTO, ("smsto:" + Uri.encode(who.number)).toUri())
                .setPackage(SIGNAL).putExtra("sms_body", message).putExtra(Intent.EXTRA_TEXT, message)
            val share = Intent(Intent.ACTION_SEND).setType("text/plain").setPackage(SIGNAL).putExtra(Intent.EXTRA_TEXT, message)
            when {
                assistant.launch(sendTo) -> ok().put("to", who.name).put("note", "Signal opened with the message ready, the user taps send")
                assistant.launch(share) -> ok().put("to", who.name).put("note", "Signal opened with the message; the user picks ${who.name} and taps send")
                else -> err("could not open Signal")
            }
        } else {
            if (!granted(Manifest.permission.SEND_SMS)) return noPermission("send SMS")
            try {
                @Suppress("DEPRECATION")
                val sms = if (Build.VERSION.SDK_INT >= 31) context.getSystemService(SmsManager::class.java) else SmsManager.getDefault()
                val parts = sms.divideMessage(message)
                if (parts.size > 1) sms.sendMultipartTextMessage(who.number, null, parts, null, null)
                else sms.sendTextMessage(who.number, null, message, null, null)
                ok().put("sent_to", who.name).put("number", who.number)
            } catch (ex: Exception) {
                err("SMS failed: ${ex.message}")
            }
        }
    }

    private fun call(contact: String): JSONObject {
        val (who, e) = resolveContact(contact)
        if (who == null) return e!!
        val uri = ("tel:" + Uri.encode(who.number)).toUri()
        if (granted(Manifest.permission.CALL_PHONE) && assistant.launch(Intent(Intent.ACTION_CALL, uri)))
            return ok().put("calling", who.name).put("number", who.number)
        return if (assistant.launch(Intent(Intent.ACTION_DIAL, uri)))
            ok().put("note", "dialer opened with ${who.name}'s number, the user taps call (call permission not granted)")
        else err("no phone app")
    }

    private fun findContact(name: String): JSONObject {
        if (!contacts.granted()) return noPermission("read contacts")
        val ranked = contacts.find(name)
        if (ranked.isEmpty()) return err("no contact named $name")
        val arr = JSONArray()
        for (m in ranked.take(5)) {
            arr.put(JSONObject().put("name", m.item.name).put("numbers", JSONArray().apply {
                m.item.numbers.forEach { put("${it.number} (${it.type})") }
            }))
        }
        return JSONObject().put("contacts", arr)
    }

    // ------------------------------------------------------------------ navigation, apps, web
    private fun navigate(destination: String, mode: String): JSONObject {
        if (destination.isEmpty()) return err("no destination")
        if (assistant.navigateOnSurface(destination, mode)) return ok().put("note", "navigation started on the car screen")
        val q = Uri.encode(destination)
        val m = when (mode) { "walking" -> "w"; "bicycling" -> "b"; "transit" -> "transit"; else -> "d" }
        val first = if (m == "transit")
            Intent(Intent.ACTION_VIEW, "https://www.google.com/maps/dir/?api=1&destination=$q&travelmode=transit".toUri())
                .setPackage("com.google.android.apps.maps")
        else Intent(Intent.ACTION_VIEW, "google.navigation:q=$q&mode=$m".toUri())
        if (assistant.launch(first)) return ok().put("destination", destination).put("mode", mode)
        // any maps app: Waze, OsmAnd, Organic Maps...
        if (assistant.launch(Intent(Intent.ACTION_VIEW, "geo:0,0?q=$q".toUri())))
            return ok().put("note", "maps app opened on the destination; the user starts the guidance")
        return err("no maps app on the phone")
    }

    private fun openApp(name: String): JSONObject {
        if (name.isEmpty()) return err("no app name")
        val tv = assistant.isTv
        val ranked = apps.find(name, tv)
        val best = Fuzzy.pick(ranked, margin = 0.03) { a, b -> a.pkg == b.pkg }
        if (best == null) {
            return if (ranked.isEmpty()) err("no app named $name")
            else err("several apps match $name").put("candidates", JSONArray(ranked.take(5).map { it.item.label }))
        }
        return if (assistant.launch(apps.launchIntent(best, tv))) ok().put("opened", best.label)
        else err("could not open ${best.label}")
    }

    /**
     * The server's "lance / mets X" fast path also catches "lance l'appareil photo": when the
     * query is plainly an app's name, say so (the server then lets the model open the app).
     */
    private fun playMusic(query: String, kind: String): JSONObject {
        if (kind == "auto" && query.isNotBlank()) {
            val app = apps.find(query, assistant.isTv).firstOrNull()
            if (app != null && app.score >= 0.9)
                return err("\"$query\" is an app on the phone (${app.item.label}), not music: use open_app")
        }
        return music.play(query, kind)
    }

    private fun openUrl(raw: String): JSONObject {
        if (raw.isEmpty()) return err("no url")
        val url = if (Regex("^[a-zA-Z][a-zA-Z0-9+.-]*:").containsMatchIn(raw)) raw else "https://$raw"
        return if (assistant.launch(Intent(Intent.ACTION_VIEW, url.toUri()))) ok() else err("no app can open $url")
    }

    // ------------------------------------------------------------------ clock
    private fun alarm(hour: Int, minute: Int, label: String): JSONObject {
        if (hour !in 0..23 || minute !in 0..59) return err("invalid time")
        val i = Intent(AlarmClock.ACTION_SET_ALARM)
            .putExtra(AlarmClock.EXTRA_HOUR, hour).putExtra(AlarmClock.EXTRA_MINUTES, minute)
            .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        return if (assistant.launch(i)) ok().put("alarm", "%02d:%02d".format(hour, minute)) else err("no clock app")
    }

    private fun timer(seconds: Int, label: String): JSONObject {
        if (seconds <= 0) return err("invalid duration")
        val i = Intent(AlarmClock.ACTION_SET_TIMER)
            .putExtra(AlarmClock.EXTRA_LENGTH, seconds).putExtra(AlarmClock.EXTRA_SKIP_UI, true)
        if (label.isNotEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label)
        return if (assistant.launch(i)) ok().put("seconds", seconds) else err("no clock app")
    }

    // ------------------------------------------------------------------ hardware
    private fun flashlight(on: Boolean): JSONObject {
        val cm = context.getSystemService(CameraManager::class.java)
        return try {
            val id = cm.cameraIdList.firstOrNull {
                cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
            } ?: return err("this phone has no flashlight")
            cm.setTorchMode(id, on)
            ok().put("flashlight", if (on) "on" else "off")
        } catch (e: Exception) {
            err("flashlight failed: ${e.message}")
        }
    }

    private fun battery(): JSONObject {
        val bm = context.getSystemService(BatteryManager::class.java)
        val level = bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        val st = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val status = st?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        val r = JSONObject().put("battery", level).put("charging", charging)
        if (Build.VERSION.SDK_INT >= 28 && charging) {
            val ms = bm.computeChargeTimeRemaining()
            if (ms > 0) r.put("minutes_to_full", ms / 60000)
        }
        return r
    }

    @SuppressLint("MissingPermission")      // checked below
    private fun location(): JSONObject {
        val fine = granted(Manifest.permission.ACCESS_FINE_LOCATION)
        if (!fine && !granted(Manifest.permission.ACCESS_COARSE_LOCATION)) return noPermission("use the location")
        val lm = context.getSystemService(LocationManager::class.java)
        val providers = lm.getProviders(true)
        var loc: Location? = providers.mapNotNull { runCatching { lm.getLastKnownLocation(it) }.getOrNull() }
            .maxByOrNull { it.elapsedRealtimeNanos }
        val ageS = { l: Location -> (SystemClock.elapsedRealtimeNanos() - l.elapsedRealtimeNanos) / 1_000_000_000 }
        if (loc == null || ageS(loc) > 300) {
            val provider = when {
                LocationManager.GPS_PROVIDER in providers && fine -> LocationManager.GPS_PROVIDER
                LocationManager.NETWORK_PROVIDER in providers -> LocationManager.NETWORK_PROVIDER
                else -> providers.firstOrNull()
            }
            if (provider != null && Build.VERSION.SDK_INT >= 30) {
                val latch = CountDownLatch(1)
                var fresh: Location? = null
                val cancel = CancellationSignal()
                val exec = Executors.newSingleThreadExecutor()
                lm.getCurrentLocation(provider, cancel, exec) { fresh = it; latch.countDown() }
                if (!latch.await(8, TimeUnit.SECONDS)) cancel.cancel()
                exec.shutdown()
                if (fresh != null) loc = fresh
            }
        }
        if (loc == null) return err(if (providers.isEmpty()) "location is turned off on the phone" else "location not available right now")
        val r = JSONObject()
            .put("latitude", "%.5f".format(Locale.US, loc.latitude).toDouble())
            .put("longitude", "%.5f".format(Locale.US, loc.longitude).toDouble())
            .put("accuracy_m", loc.accuracy.toInt())
            .put("age_s", ageS(loc))
        if (Geocoder.isPresent()) try {
            @Suppress("DEPRECATION")
            Geocoder(context, Locale.getDefault()).getFromLocation(loc.latitude, loc.longitude, 1)?.firstOrNull()?.let { a ->
                r.put("address", (0..a.maxAddressLineIndex).joinToString(", ") { a.getAddressLine(it) })
                a.locality?.let { r.put("city", it) }
            }
        } catch (_: Exception) { }
        return r
    }

    private fun dnd(on: Boolean): JSONObject {
        val nm = context.getSystemService(NotificationManager::class.java)
        if (!nm.isNotificationPolicyAccessGranted)
            return err("Do Not Disturb access not granted; the user must open the Lapin app and tap \"Do Not Disturb access\" " +
                "(Settings > Apps > Special app access > Do Not Disturb, allow Lapin)")
        nm.setInterruptionFilter(if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY else NotificationManager.INTERRUPTION_FILTER_ALL)
        return ok().put("do_not_disturb", if (on) "on" else "off")
    }

    // ------------------------------------------------------------------ calendar
    private fun calendar(title: String, startIso: String, endIso: String, location: String): JSONObject {
        if (title.isEmpty()) return err("no title")
        val start = parseTime(startIso) ?: return err("start_iso is not an ISO 8601 date/time: $startIso")
        val end = if (endIso.isNotEmpty()) parseTime(endIso) ?: return err("end_iso is not an ISO 8601 date/time: $endIso") else null
        val i = Intent(Intent.ACTION_INSERT, CalendarContract.Events.CONTENT_URI)
            .putExtra(CalendarContract.Events.TITLE, title)
            .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, start.first)
        if (start.second) {
            i.putExtra(CalendarContract.EXTRA_EVENT_ALL_DAY, true)
            i.putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end?.first ?: (start.first + 86_400_000))
        } else {
            i.putExtra(CalendarContract.EXTRA_EVENT_END_TIME, end?.first ?: (start.first + 3_600_000))
        }
        if (location.isNotEmpty()) i.putExtra(CalendarContract.Events.EVENT_LOCATION, location)
        return if (assistant.launch(i)) ok().put("note", "calendar opened with the event filled in; the user taps save")
        else err("no calendar app")
    }

    /** ISO 8601 -> (epoch ms, all-day). */
    private fun parseTime(s: String): Pair<Long, Boolean>? {
        val zone = ZoneId.systemDefault()
        return runCatching { OffsetDateTime.parse(s).toInstant().toEpochMilli() to false }.getOrNull()
            ?: runCatching { ZonedDateTime.parse(s).toInstant().toEpochMilli() to false }.getOrNull()
            ?: runCatching { LocalDateTime.parse(s).atZone(zone).toInstant().toEpochMilli() to false }.getOrNull()
            ?: runCatching { LocalDate.parse(s).atStartOfDay(zone).toInstant().toEpochMilli() to true }.getOrNull()
    }

    companion object {
        const val SIGNAL = "org.thoughtcrime.securesms"
    }
}

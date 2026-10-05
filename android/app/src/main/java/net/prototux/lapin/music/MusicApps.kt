package net.prototux.lapin.music

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.provider.MediaStore

/** Music players on the phone: apps with a media browser service (media3 or legacy) or handling "play from search". */
class MusicApps(private val context: Context) {
    data class App(val pkg: String, val label: String, val service: ComponentName?, val searchActivity: Boolean)

    fun all(): List<App> {
        val pm = context.packageManager
        val services = LinkedHashMap<String, ComponentName>()
        for (action in SERVICE_ACTIONS) {
            @Suppress("DEPRECATION")
            for (ri in pm.queryIntentServices(Intent(action), 0)) {
                val si = ri.serviceInfo ?: continue
                services.putIfAbsent(si.packageName, ComponentName(si.packageName, si.name))
            }
        }
        @Suppress("DEPRECATION")
        val searchPkgs = pm.queryIntentActivities(Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH), 0)
            .map { it.activityInfo.packageName }.toSet()
        return (services.keys + searchPkgs).filter { it != context.packageName }.map { pkg ->
            App(pkg, label(pkg), services[pkg], pkg in searchPkgs)
        }.sortedWith(compareBy({ !MusicPick.Tempus.isTempus(it.pkg) }, { it.label.lowercase() }))
    }

    /** The configured app, else Tempus if installed, else null. */
    fun chosen(pref: String): App? {
        val apps = all()
        return apps.firstOrNull { it.pkg == pref } ?: apps.firstOrNull { MusicPick.Tempus.isTempus(it.pkg) }
    }

    fun label(pkg: String): String = try {
        val pm = context.packageManager
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    } catch (_: PackageManager.NameNotFoundException) { pkg }

    companion object {
        val SERVICE_ACTIONS = listOf(
            "androidx.media3.session.MediaLibraryService",
            "androidx.media3.session.MediaSessionService",
            "android.media.browse.MediaBrowserService",
        )
    }
}

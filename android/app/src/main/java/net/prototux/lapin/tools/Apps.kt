package net.prototux.lapin.tools

import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager

/** Launchable apps, matched by label (French/English synonyms, accents-insensitive). */
class Apps(private val context: Context) {
    data class App(val label: String, val pkg: String, val activity: String)

    fun all(tv: Boolean): List<App> {
        val pm = context.packageManager
        val out = LinkedHashMap<String, App>()
        val cats = if (tv) listOf(Intent.CATEGORY_LEANBACK_LAUNCHER, Intent.CATEGORY_LAUNCHER) else listOf(Intent.CATEGORY_LAUNCHER)
        for (cat in cats) {
            val intent = Intent(Intent.ACTION_MAIN).addCategory(cat)
            @Suppress("DEPRECATION")
            for (ri in pm.queryIntentActivities(intent, PackageManager.MATCH_ALL)) {
                val pkg = ri.activityInfo.packageName
                if (pkg == context.packageName || out.containsKey(pkg)) continue
                out[pkg] = App(ri.loadLabel(pm).toString(), pkg, ri.activityInfo.name)
            }
        }
        return out.values.toList()
    }

    fun find(name: String, tv: Boolean): List<Fuzzy.Match<App>> {
        val variants = Fuzzy.appVariants(name)
        return all(tv).map { app ->
            val names = listOf(app.label, app.pkg.substringAfterLast('.'))
            Fuzzy.Match(app, variants.maxOf { v -> Fuzzy.scoreAny(v, names) - if (v == name) 0.0 else 0.02 })
        }.filter { it.score >= 0.72 }.sortedByDescending { it.score }
    }

    fun launchIntent(app: App, tv: Boolean): Intent {
        val pm = context.packageManager
        return (if (tv) pm.getLeanbackLaunchIntentForPackage(app.pkg) else null)
            ?: pm.getLaunchIntentForPackage(app.pkg)
            ?: Intent(Intent.ACTION_MAIN).setClassName(app.pkg, app.activity)
    }
}

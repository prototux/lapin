package net.prototux.lapin.tools

import java.text.Normalizer
import kotlin.math.max
import kotlin.math.min

/**
 * Fuzzy, accent-insensitive name matching for contacts and apps.
 * Pure Kotlin (unit tested).
 */
object Fuzzy {
    private val MARKS = Regex("\\p{Mn}+")
    private val NON_ALNUM = Regex("[^a-z0-9]+")

    /** Fillers people put around a name: "l'appli Spotify", "my phone app", "à maman". */
    private val STOP = setOf(
        "app", "appli", "application", "l", "le", "la", "les", "de", "du", "des", "d", "the", "my", "mon", "ma",
        "mes", "a", "au", "aux", "to",
    )

    fun normalize(s: String): String {
        val noAccents = MARKS.replace(Normalizer.normalize(s.lowercase(), Normalizer.Form.NFD), "")
            .replace("œ", "oe").replace("æ", "ae").replace("ß", "ss")
        return NON_ALNUM.replace(noAccents, " ").trim()
    }

    fun tokens(s: String): List<String> = normalize(s).split(' ').filter { it.isNotEmpty() }

    private fun queryTokens(s: String): List<String> {
        val t = tokens(s)
        val kept = t.filter { it !in STOP }
        return kept.ifEmpty { t }
    }

    fun levenshtein(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length
        var prev = IntArray(b.length + 1) { it }
        var cur = IntArray(b.length + 1)
        for (i in 1..a.length) {
            cur[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                cur[j] = min(min(cur[j - 1] + 1, prev[j] + 1), prev[j - 1] + cost)
            }
            val t = prev; prev = cur; cur = t
        }
        return prev[b.length]
    }

    fun ratio(a: String, b: String): Double {
        val m = max(a.length, b.length)
        return if (m == 0) 1.0 else 1.0 - levenshtein(a, b).toDouble() / m
    }

    private fun tokenSim(q: String, c: String): Double = when {
        q == c -> 1.0
        q.length >= 3 && c.startsWith(q) -> 0.85
        else -> ratio(q, c)
    }

    /** Similarity of a spoken name to a candidate name, 0..1. */
    fun score(query: String, candidate: String): Double {
        val qt = queryTokens(query)
        val ct = tokens(candidate)
        if (qt.isEmpty() || ct.isEmpty()) return 0.0
        val q = qt.joinToString(" ")
        val c = ct.joinToString(" ")
        if (q == c) return 1.0
        if (q.replace(" ", "") == c.replace(" ", "")) return 0.98      // "whats app" / "WhatsApp"
        var best = ratio(q, c)
        // every spoken word is a word of the name ("marie" -> "Marie Dupont")
        if (qt.all { it in ct }) best = max(best, 0.92 - 0.01 * (ct.size - qt.size).coerceAtMost(5))
        // every word of the name was said ("google maps" -> "Maps")
        if (ct.all { it in qt }) best = max(best, 0.84 - 0.02 * (qt.size - ct.size).coerceAtMost(5))
        if (q.length >= 3 && c.startsWith(q)) best = max(best, 0.86)
        // word by word, best pairing
        val perWord = qt.map { w -> ct.maxOf { tokenSim(w, it) } }.average()
        best = max(best, perWord * if (qt.size <= ct.size) 0.95 else 0.85)
        return best.coerceIn(0.0, 1.0)
    }

    /** Best score over several names (label, aliases...). */
    fun scoreAny(query: String, names: Collection<String>): Double =
        names.maxOfOrNull { score(query, it) } ?: 0.0

    data class Match<T>(val item: T, val score: Double)

    /** Items scoring at least [min], best first. */
    fun <T> rank(query: String, items: Collection<T>, min: Double = 0.7, names: (T) -> Collection<String>): List<Match<T>> =
        items.map { Match(it, scoreAny(query, names(it))) }
            .filter { it.score >= min }
            .sortedByDescending { it.score }

    /**
     * The match to use, or null when the top matches are too close to call
     * (then ask the user which one). [sameItem] tells whether two items are
     * the same thing (one contact listed twice, for instance).
     */
    fun <T> pick(ranked: List<Match<T>>, margin: Double = 0.05, sameItem: (T, T) -> Boolean = { a, b -> a == b }): T? {
        val top = ranked.firstOrNull() ?: return null
        if (top.score >= 0.999) {
            // exact matches: unique unless several different items match exactly
            val exact = ranked.filter { it.score >= 0.999 }
            return if (exact.all { sameItem(it.item, top.item) }) top.item else null
        }
        val close = ranked.filter { top.score - it.score < margin }
        return if (close.all { sameItem(it.item, top.item) }) top.item else null
    }

    // ------------------------------------------------------------------ app name synonyms (fr/en)
    private val APP_SYNONYMS: List<Set<String>> = listOf(
        setOf("settings", "parametres", "reglages", "parametre"),
        setOf("camera", "appareil photo", "camera app"),
        setOf("photos", "galerie", "gallery", "google photos", "album"),
        setOf("clock", "horloge", "reveil", "alarme", "alarm", "minuteur"),
        setOf("phone", "telephone", "appels", "dialer", "appel"),
        setOf("messages", "sms", "messagerie", "textos", "messaging"),
        setOf("contacts", "repertoire", "carnet d adresses"),
        setOf("calendar", "agenda", "calendrier"),
        setOf("calculator", "calculatrice", "calculette"),
        setOf("maps", "cartes", "google maps", "plans", "carte"),
        setOf("music", "musique"),
        setOf("files", "fichiers", "gestionnaire de fichiers", "file manager"),
        setOf("chrome", "navigateur", "browser", "internet", "web"),
        setOf("play store", "google play", "store", "play store app"),
        setOf("gmail", "mail", "email", "e mail", "courrier", "mails"),
        setOf("weather", "meteo"),
        setOf("keep", "notes", "keep notes", "bloc notes"),
        setOf("recorder", "magnetophone", "dictaphone", "enregistreur"),
        setOf("youtube", "you tube"),
        setOf("signal"), setOf("whatsapp", "whats app"),
    )

    /** The name plus its translations / synonyms ("paramètres" -> settings, réglages). */
    fun appVariants(name: String): List<String> {
        val n = queryTokens(name).joinToString(" ")
        val out = linkedSetOf(name)
        for (g in APP_SYNONYMS) if (n in g) out += g
        return out.toList()
    }
}

package net.prototux.lapin.music

import net.prototux.lapin.tools.Fuzzy
import org.json.JSONObject

/**
 * Choosing what to play among a music app's search / browse results, and the
 * tool results. Pure Kotlin (unit tested); the media3 side is in [MusicPlayer].
 */
object MusicPick {
    enum class Type { ARTIST, ALBUM, SONG, PLAYLIST, GENRE, OTHER }

    data class Item(
        val id: String,
        val title: String,
        val artist: String = "",
        val type: Type = Type.OTHER,
        val playable: Boolean = false,
        val browsable: Boolean = false,
    )

    val KINDS = listOf("auto", "artist", "album", "playlist", "song", "genre", "random")

    /** Tempus' browse ids (com.eddyizm.tempus, ConstantsAA). */
    object Tempus {
        val PACKAGES = listOf("com.eddyizm.tempus", "com.eddyizm.degoogled.tempus", "com.eddyizm.tempus.debug", "com.eddyizm.degoogled.tempus.debug")
        const val ARTIST = "[artistID]"
        const val ALBUM = "[albumID]"
        const val PLAYLISTS = "[playlistID]"
        const val GENRES = "[genresID]"
        const val RANDOM = "[randomID]"
        const val INSTANT_MIX = "[instantMixSource]"
        fun isTempus(pkg: String?) = pkg != null && pkg in PACKAGES
    }

    /** Type from media3's MediaMetadata.mediaType (constants passed in to stay Android-free) or Tempus id prefixes. */
    fun typeOf(id: String, mediaType: Int?, playable: Boolean, t: MediaTypes): Type = when {
        id.startsWith(Tempus.ARTIST) || mediaType == t.artist -> Type.ARTIST
        id.startsWith(Tempus.ALBUM) || mediaType == t.album -> Type.ALBUM
        id.startsWith(Tempus.GENRES) || mediaType == t.genre -> Type.GENRE
        id.startsWith(Tempus.PLAYLISTS) || mediaType == t.playlist -> Type.PLAYLIST
        playable || mediaType == t.music -> Type.SONG
        else -> Type.OTHER
    }

    data class MediaTypes(val artist: Int, val album: Int, val genre: Int, val playlist: Int, val music: Int)

    /** Words around the name that people say: "l'album Discovery", "du Daft Punk", "la chanson X". */
    fun cleanQuery(q: String): String =
        q.trim().replace(Regex("^(?i)(de la |du |de l'|des |de |le |la |les |l')"), "").trim()

    /** Queries to try in turn: "Around the World de Daft Punk" -> also "Around the World", "Daft Punk". */
    fun searchVariants(query: String): List<String> {
        val q = cleanQuery(query)
        val out = linkedSetOf(query.trim(), q)
        Regex("^(.+?)\\s+(?:de|by|par|from|-)\\s+(.+)$", RegexOption.IGNORE_CASE).find(q)?.let {
            out += it.groupValues[1].trim(); out += it.groupValues[2].trim()
        }
        return out.filter { it.isNotBlank() }
    }

    private fun accepted(kind: String): Set<Type> = when (kind) {
        "artist" -> setOf(Type.ARTIST)
        "album" -> setOf(Type.ALBUM)
        "song" -> setOf(Type.SONG)
        "playlist" -> setOf(Type.PLAYLIST)
        "genre" -> setOf(Type.GENRE)
        else -> setOf(Type.ARTIST, Type.ALBUM, Type.SONG, Type.PLAYLIST, Type.GENRE)
    }

    fun score(query: String, item: Item): Double {
        val names = mutableListOf(item.title)
        if (item.artist.isNotBlank() && item.type != Type.ARTIST) {
            names += "${item.title} ${item.artist}"; names += "${item.artist} ${item.title}"
        }
        return Fuzzy.scoreAny(cleanQuery(query), names)
    }

    /** The best result for the request, or null if nothing is close enough. */
    fun best(query: String, kind: String, items: List<Item>, min: Double = 0.7): Item? {
        val ok = accepted(kind)
        // on a tie, the broadest: "mets du Daft Punk" is the artist, not a song called "Daft Punk"
        val bonus = mapOf(Type.ARTIST to 0.03, Type.ALBUM to 0.02, Type.PLAYLIST to 0.015, Type.GENRE to 0.01, Type.SONG to 0.0)
        return items.filter { it.type in ok }
            .map { it to score(query, it) }
            .filter { it.second >= min }
            .maxByOrNull { it.second + (bonus[it.first.type] ?: 0.0) }?.first
    }

    /** What is about to play, for the model: short, in English. */
    fun describe(item: Item?, kind: String): String = when {
        kind == "random" || item == null -> "random songs"
        item.type == Type.ARTIST -> "songs by ${item.title}"
        item.type == Type.ALBUM -> "the album ${item.title}" + if (item.artist.isNotBlank()) " by ${item.artist}" else ""
        item.type == Type.PLAYLIST -> "the playlist ${item.title}"
        item.type == Type.GENRE -> "${item.title} music"
        else -> item.title + if (item.artist.isNotBlank()) " by ${item.artist}" else ""
    }

    fun playResult(what: String, app: String): JSONObject = JSONObject().put("ok", true).put("playing", what).put("app", app)

    fun error(message: String): JSONObject = JSONObject().put("error", message)

    /** phone_now_playing: {"title","artist","album","app","playing"}; nothing loaded -> playing false, no title. */
    fun nowPlayingResult(title: String?, artist: String?, album: String?, app: String, playing: Boolean): JSONObject {
        val r = JSONObject().put("app", app)
        if (title.isNullOrBlank()) return r.put("playing", false)
        return r.put("title", title).put("artist", artist ?: "").put("album", album ?: "").put("playing", playing)
    }
}

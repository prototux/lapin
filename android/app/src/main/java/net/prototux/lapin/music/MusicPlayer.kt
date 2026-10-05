package net.prototux.lapin.music

import android.app.SearchManager
import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.media.session.MediaController as FwController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.HandlerThread
import android.os.SystemClock
import android.provider.MediaStore
import android.util.Log
import android.view.KeyEvent
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.session.LibraryResult
import androidx.media3.session.MediaBrowser
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import net.prototux.lapin.TAG
import net.prototux.lapin.music.MusicPick.Item
import net.prototux.lapin.music.MusicPick.Tempus
import net.prototux.lapin.music.MusicPick.Type
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Music in the user's own player app (Tempus by default): search and play
 * through its media3 MediaLibraryService (it accepts external controllers),
 * transport controls and "what's playing" on its session. Fallbacks: the
 * "play from search" intent, the active media sessions (with notification
 * access), media keys. Blocking; call off the main thread.
 */
class MusicPlayer(private val context: Context, private val musicPref: () -> String, private val launch: (Intent) -> Boolean) {
    private val apps = MusicApps(context)
    private val thread = HandlerThread("lapin-media").apply { start() }
    private val handler = Handler(thread.looper)
    private val types = MusicPick.MediaTypes(
        MediaMetadata.MEDIA_TYPE_ARTIST, MediaMetadata.MEDIA_TYPE_ALBUM, MediaMetadata.MEDIA_TYPE_GENRE,
        MediaMetadata.MEDIA_TYPE_PLAYLIST, MediaMetadata.MEDIA_TYPE_MUSIC,
    )

    private fun <T> onLooper(block: () -> T): T {
        var r: Result<T>? = null
        val latch = CountDownLatch(1)
        handler.post { r = runCatching(block); latch.countDown() }
        if (!latch.await(8, TimeUnit.SECONDS)) throw IllegalStateException("media thread busy")
        return r!!.getOrThrow()
    }

    private fun <T> ListenableFuture<T>.await(ms: Long): T = get(ms.coerceAtLeast(200), TimeUnit.MILLISECONDS)

    // ------------------------------------------------------------------ play
    fun play(query: String, kindArg: String): JSONObject {
        val kind = kindArg.lowercase().takeIf { it in MusicPick.KINDS } ?: "auto"
        val app = apps.chosen(musicPref()) ?: return searchIntent(null, query, kind)
            ?: MusicPick.error("no music app found on the phone (install Tempus or pick a music app in Lapin's settings)")
        if (app.service == null) return searchIntent(app, query, kind) ?: MusicPick.error("${app.label} can't be asked to play music")
        val deadline = SystemClock.elapsedRealtime() + 17_000
        val left = { deadline - SystemClock.elapsedRealtime() }
        val browser = try {
            val token = SessionToken(context, app.service)
            onLooper { MediaBrowser.Builder(context, token).setApplicationLooper(thread.looper).buildAsync() }.await(6000)
        } catch (e: Exception) {
            Log.w(TAG, "music: can't connect to ${app.pkg}: $e")
            return searchIntent(app, query, kind) ?: MusicPick.error("can't connect to ${app.label}")
        }
        try {
            val tempus = Tempus.isTempus(app.pkg)
            fun children(id: String): List<MediaItem> {
                val r = onLooper { browser.getChildren(id, 0, 200, null) }.await(left())
                return if (r.resultCode == LibraryResult.RESULT_SUCCESS) r.value.orEmpty() else emptyList()
            }
            fun search(q: String): List<MediaItem> {
                runCatching { onLooper { browser.search(q, null) }.await(minOf(4000, left())) }
                val r = onLooper { browser.getSearchResult(q, 0, 60, null) }.await(minOf(8000, left()))
                return if (r.resultCode == LibraryResult.RESULT_SUCCESS) r.value.orEmpty() else emptyList()
            }
            fun pickAmong(list: List<MediaItem>, k: String, q: String): Pair<Item, MediaItem>? {
                val items = list.map { it to item(it) }
                val best = MusicPick.best(q, k, items.map { it.second }) ?: return null
                return items.first { it.second === best }.let { it.second to it.first }
            }

            var picked: Item? = null
            var queue: List<MediaItem> = emptyList()
            var start = 0
            if (kind == "random" || query.isBlank()) {
                if (!tempus) return searchIntent(app, "", "random") ?: MusicPick.error("${app.label} can't play random music on request")
                queue = children(Tempus.RANDOM).filter { it.mediaMetadata.isPlayable == true }
            } else {
                var hit: Pair<Item, MediaItem>? = null
                var pool: List<MediaItem> = emptyList()
                if (tempus && kind == "playlist") { pool = children(Tempus.PLAYLISTS).map { forceType(it, Type.PLAYLIST) }; hit = pickAmong(pool, kind, query) }
                else if (tempus && kind == "genre") { pool = children(Tempus.GENRES).map { forceType(it, Type.GENRE) }; hit = pickAmong(pool, kind, query) }
                else for (q in MusicPick.searchVariants(query)) {
                    pool = search(q)
                    hit = pickAmong(pool, kind, query)
                    if (hit != null || left() < 4000) break
                }
                if (hit == null && tempus && kind == "auto" && left() > 4000) {
                    // not an artist/album/song: maybe one of the user's playlists or a genre
                    for (root in listOf(Tempus.PLAYLISTS to Type.PLAYLIST, Tempus.GENRES to Type.GENRE)) {
                        pool = children(root.first).map { forceType(it, root.second) }
                        hit = pickAmong(pool, "auto", query)
                        if (hit != null) break
                    }
                }
                if (hit == null) return MusicPick.error("nothing found for \"$query\" in ${app.label}")
                picked = hit.first
                val media = hit.second
                if (media.mediaMetadata.isPlayable == true) {
                    queue = pool.filter { it.mediaMetadata.isPlayable == true }
                    start = queue.indexOfFirst { it.mediaId == media.mediaId }.coerceAtLeast(0)
                } else {
                    val kids = children(media.mediaId)
                    val mix = kids.firstOrNull { it.mediaId.startsWith(Tempus.INSTANT_MIX) }
                    val songs = kids.filter { it.mediaMetadata.isPlayable == true }
                    queue = when {
                        mix != null -> listOf(mix)          // Tempus: a shuffle of the artist's own tracks
                        songs.isNotEmpty() -> songs
                        else -> kids.firstOrNull { it.mediaMetadata.isBrowsable == true }
                            ?.let { album -> children(album.mediaId).filter { it.mediaMetadata.isPlayable == true } }.orEmpty()
                    }
                }
            }
            if (queue.isEmpty()) return MusicPick.error("nothing playable found for \"$query\" in ${app.label}")
            Log.i(TAG, "music: ${app.pkg} plays ${MusicPick.describe(picked, kind)} (${queue.size} items, start $start)")
            onLooper {
                browser.setMediaItems(queue, start, 0)
                browser.prepare()
                browser.play()
            }
            // the app resolves the items asynchronously: wait for it to start (or fail)
            var playing = false
            var error: String? = null
            while (left() > 0) {
                val st = onLooper { browser.isPlaying to browser.playerError?.message }
                if (st.first) { playing = true; break }
                if (st.second != null) { error = st.second; break }
                Thread.sleep(150)
            }
            if (error != null) return MusicPick.error("${app.label} could not play it: $error")
            val r = MusicPick.playResult(MusicPick.describe(picked, kind), app.label)
            if (!playing) r.put("note", "asked ${app.label} to play; playback not confirmed yet")
            return r
        } catch (e: Exception) {
            Log.w(TAG, "music: $e")
            return MusicPick.error("${app.label} did not answer: ${e.message ?: e.javaClass.simpleName}")
        } finally {
            runCatching { onLooper { browser.release() } }
        }
    }

    private fun item(m: MediaItem): Item {
        val md = m.mediaMetadata
        val playable = md.isPlayable == true
        return Item(
            m.mediaId, (md.title ?: md.displayTitle ?: "").toString(),
            (md.artist ?: md.albumArtist ?: md.subtitle ?: "").toString(),
            MusicPick.typeOf(m.mediaId, md.mediaType, playable, types), playable, md.isBrowsable == true,
        )
    }

    private fun forceType(m: MediaItem, t: Type): MediaItem =
        m.buildUpon().setMediaMetadata(m.mediaMetadata.buildUpon()
            .setMediaType(if (t == Type.GENRE) MediaMetadata.MEDIA_TYPE_GENRE else MediaMetadata.MEDIA_TYPE_PLAYLIST).build()).build()

    /** MEDIA_PLAY_FROM_SEARCH for apps that handle it in an activity (Spotify, YouTube Music...); null if none does. */
    private fun searchIntent(app: MusicApps.App?, query: String, kind: String): JSONObject? {
        if (app != null && !app.searchActivity) return null
        val focus = when (kind) {
            "artist" -> MediaStore.Audio.Artists.ENTRY_CONTENT_TYPE
            "album" -> MediaStore.Audio.Albums.ENTRY_CONTENT_TYPE
            "playlist" -> "vnd.android.cursor.item/playlist"
            "genre" -> MediaStore.Audio.Genres.ENTRY_CONTENT_TYPE
            "song" -> "vnd.android.cursor.item/audio"
            "random" -> "vnd.android.cursor.item/*"
            else -> null
        }
        val q = if (kind == "random") "" else MusicPick.cleanQuery(query)
        val i = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).putExtra(SearchManager.QUERY, q)
        if (focus != null) i.putExtra(MediaStore.EXTRA_MEDIA_FOCUS, focus)
        when (kind) {
            "artist" -> i.putExtra(MediaStore.EXTRA_MEDIA_ARTIST, q)
            "album" -> i.putExtra(MediaStore.EXTRA_MEDIA_ALBUM, q)
            "song" -> i.putExtra(MediaStore.EXTRA_MEDIA_TITLE, q)
            "genre" -> i.putExtra("android.intent.extra.genre", q)
            "playlist" -> i.putExtra("android.intent.extra.playlist", q)
        }
        if (app != null) i.setPackage(app.pkg)
        if (!launch(i)) return null
        return MusicPick.playResult(if (kind == "random") "music" else q, app?.label ?: "music app")
            .put("note", "asked the app to play it; playback not confirmed")
    }

    // ------------------------------------------------------------------ control and now playing
    private fun activeSessions(): List<FwController> = try {
        if (!MediaListener.granted(context)) emptyList()
        else context.getSystemService(MediaSessionManager::class.java).getActiveSessions(MediaListener.component(context))
    } catch (e: SecurityException) { emptyList() }

    private fun FwController.isPlaying() = playbackState?.state == PlaybackState.STATE_PLAYING

    /** A media3 controller on the chosen music app's session, or null. */
    private fun <T> withAppController(block: (MediaController, MusicApps.App) -> T): T? {
        val app = apps.chosen(musicPref()) ?: return null
        val service = app.service ?: return null
        val c = try {
            onLooper { MediaController.Builder(context, SessionToken(context, service)).setApplicationLooper(thread.looper).buildAsync() }.await(5000)
        } catch (e: Exception) {
            Log.w(TAG, "music: controller for ${app.pkg}: $e"); return null
        }
        return try { onLooper { block(c, app) } } finally { runCatching { onLooper { c.release() } } }
    }

    fun control(action: String): JSONObject {
        if (action !in listOf("play", "pause", "next", "previous")) return MusicPick.error("unknown action $action")
        // 1. with notification access: the session actually playing (or the last active one)
        val sessions = activeSessions()
        val target = sessions.firstOrNull { it.isPlaying() }
            ?: sessions.firstOrNull { it.packageName == apps.chosen(musicPref())?.pkg } ?: sessions.firstOrNull()
        if (target != null) {
            val t = target.transportControls
            when (action) { "play" -> t.play(); "pause" -> t.pause(); "next" -> t.skipToNext(); else -> t.skipToPrevious() }
            return JSONObject().put("ok", true).put("action", action).put("app", apps.label(target.packageName))
        }
        // 2. the chosen music app, unless another app is the one playing
        val musicActive = context.getSystemService(AudioManager::class.java).isMusicActive
        val viaApp = withAppController { c, app ->
            val mine = c.isPlaying || (!musicActive && c.mediaItemCount > 0)
            if (!mine) null else {
                when (action) {
                    "play" -> c.play(); "pause" -> c.pause()
                    "next" -> c.seekToNext(); else -> c.seekToPrevious()
                }
                JSONObject().put("ok", true).put("action", action).put("app", app.label)
            }
        }
        if (viaApp != null) return viaApp
        // 3. media keys: whatever app has the media session
        mediaKey(action)
        return JSONObject().put("ok", true).put("action", action).put("note", "sent as a media key")
    }

    private fun mediaKey(action: String) {
        val code = when (action) {
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY; "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT; else -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
        }
        val am = context.getSystemService(AudioManager::class.java)
        val t = SystemClock.uptimeMillis()
        am.dispatchMediaKeyEvent(KeyEvent(t, t, KeyEvent.ACTION_DOWN, code, 0))
        am.dispatchMediaKeyEvent(KeyEvent(t, t + 20, KeyEvent.ACTION_UP, code, 0))
    }

    fun nowPlaying(): JSONObject {
        val sessions = activeSessions()
        (sessions.firstOrNull { it.isPlaying() } ?: sessions.firstOrNull { it.metadata != null })?.let { s ->
            val md = s.metadata
            return MusicPick.nowPlayingResult(
                md?.getString(android.media.MediaMetadata.METADATA_KEY_TITLE),
                md?.getString(android.media.MediaMetadata.METADATA_KEY_ARTIST) ?: md?.getString(android.media.MediaMetadata.METADATA_KEY_ALBUM_ARTIST),
                md?.getString(android.media.MediaMetadata.METADATA_KEY_ALBUM),
                apps.label(s.packageName), s.isPlaying(),
            )
        }
        val musicActive = context.getSystemService(AudioManager::class.java).isMusicActive
        val r = withAppController { c, app ->
            if (c.mediaItemCount == 0 || (musicActive && !c.isPlaying)) null
            else {
                val md = c.mediaMetadata
                MusicPick.nowPlayingResult(md.title?.toString(), (md.artist ?: md.albumArtist)?.toString(),
                    md.albumTitle?.toString(), app.label, c.isPlaying)
            }
        }
        if (r != null) return r
        if (musicActive) return MusicPick.error("another app is playing; allow notification access in Lapin's settings to read what it is")
        return MusicPick.nowPlayingResult(null, null, null, apps.chosen(musicPref())?.label ?: "", false)
    }
}

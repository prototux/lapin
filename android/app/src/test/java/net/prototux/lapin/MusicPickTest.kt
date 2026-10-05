package net.prototux.lapin

import net.prototux.lapin.music.MusicPick
import net.prototux.lapin.music.MusicPick.Item
import net.prototux.lapin.music.MusicPick.Type
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class MusicPickTest {
    private val types = MusicPick.MediaTypes(artist = 11, album = 10, genre = 21, playlist = 12, music = 1)

    // what Tempus' search returns for "daft punk": artists, albums, songs
    private val results = listOf(
        Item("[artistID]ar1", "Daft Punk", type = Type.ARTIST, browsable = true),
        Item("[albumID]al1", "Discovery", "Daft Punk", Type.ALBUM, browsable = true),
        Item("[albumID]al2", "Random Access Memories", "Daft Punk", Type.ALBUM, browsable = true),
        Item("s1", "Around the World", "Daft Punk", Type.SONG, playable = true),
        Item("s2", "One More Time", "Daft Punk", Type.SONG, playable = true),
        Item("s3", "Daft Punk Is Playing at My House", "LCD Soundsystem", Type.SONG, playable = true),
    )

    @Test fun typeFromTempusIdsAndMediaTypes() {
        assertEquals(Type.ARTIST, MusicPick.typeOf("[artistID]42", null, false, types))
        assertEquals(Type.ALBUM, MusicPick.typeOf("[albumID]42", null, false, types))
        assertEquals(Type.GENRE, MusicPick.typeOf("[genresID]Rock", null, false, types))
        assertEquals(Type.PLAYLIST, MusicPick.typeOf("[playlistID]7", null, false, types))
        assertEquals(Type.SONG, MusicPick.typeOf("tr-1", null, true, types))
        assertEquals(Type.ARTIST, MusicPick.typeOf("x", 11, false, types))
        assertEquals(Type.ALBUM, MusicPick.typeOf("x", 10, false, types))
        assertEquals(Type.OTHER, MusicPick.typeOf("x", null, false, types))
    }

    @Test fun autoPrefersTheArtist() {
        assertEquals("[artistID]ar1", MusicPick.best("Daft Punk", "auto", results)?.id)
        assertEquals("[artistID]ar1", MusicPick.best("du daft punk", "auto", results)?.id)
    }

    @Test fun byKind() {
        assertEquals("[albumID]al1", MusicPick.best("discovery", "album", results)?.id)
        assertEquals("[albumID]al1", MusicPick.best("Discovery", "auto", results)?.id)
        assertEquals("s1", MusicPick.best("around the world", "song", results)?.id)
        assertEquals("s1", MusicPick.best("Around the World Daft Punk", "song", results)?.id)
        assertEquals("s2", MusicPick.best("one more time", "auto", results)?.id)
        assertNull(MusicPick.best("discovery", "artist", results))
    }

    @Test fun nothingClose() {
        assertNull(MusicPick.best("Rammstein", "auto", results))
        assertNull(MusicPick.best("anything", "auto", emptyList()))
    }

    @Test fun playlistsAndGenres() {
        val pl = listOf(Item("[playlistID]1", "Running", type = Type.PLAYLIST), Item("[playlistID]2", "Chill du dimanche", type = Type.PLAYLIST))
        assertEquals("[playlistID]2", MusicPick.best("chill du dimanche", "playlist", pl)?.id)
        assertEquals("[playlistID]1", MusicPick.best("running", "auto", pl)?.id)
        val genres = listOf(Item("[genresID]Jazz", "Jazz", type = Type.GENRE), Item("[genresID]Électro", "Électro", type = Type.GENRE))
        assertEquals("[genresID]Électro", MusicPick.best("electro", "genre", genres)?.id)
    }

    @Test fun searchVariants() {
        assertEquals(listOf("Around the World de Daft Punk", "Around the World", "Daft Punk"), MusicPick.searchVariants("Around the World de Daft Punk"))
        assertEquals(listOf("du Daft Punk", "Daft Punk"), MusicPick.searchVariants("du Daft Punk"))
        assertEquals(listOf("Daft Punk"), MusicPick.searchVariants("Daft Punk"))
    }

    @Test fun describe() {
        assertEquals("songs by Daft Punk", MusicPick.describe(results[0], "auto"))
        assertEquals("the album Discovery by Daft Punk", MusicPick.describe(results[1], "album"))
        assertEquals("Around the World by Daft Punk", MusicPick.describe(results[3], "song"))
        assertEquals("random songs", MusicPick.describe(null, "random"))
    }

    @Test fun playResult() {
        val r = MusicPick.playResult("songs by Daft Punk", "Tempus")
        assertTrue(r.getBoolean("ok"))
        assertEquals("songs by Daft Punk", r.getString("playing"))
        assertEquals("Tempus", r.getString("app"))
        assertEquals("nothing found", MusicPick.error("nothing found").getString("error"))
    }

    @Test fun nowPlayingResult() {
        val r = MusicPick.nowPlayingResult("One More Time", "Daft Punk", "Discovery", "Tempus", true)
        assertEquals("One More Time", r.getString("title"))
        assertEquals("Daft Punk", r.getString("artist"))
        assertEquals("Discovery", r.getString("album"))
        assertEquals("Tempus", r.getString("app"))
        assertTrue(r.getBoolean("playing"))
        assertFalse(r.has("error"))
        // nothing loaded: no title, playing false, and no error (the server then says "nothing is playing")
        val none = MusicPick.nowPlayingResult(null, null, null, "Tempus", false)
        assertFalse(none.has("title"))
        assertFalse(none.getBoolean("playing"))
        assertFalse(none.has("error"))
        val paused = MusicPick.nowPlayingResult("Aerodynamic", null, null, "Tempus", false)
        assertEquals("", paused.getString("artist"))
        assertFalse(paused.getBoolean("playing"))
    }
}

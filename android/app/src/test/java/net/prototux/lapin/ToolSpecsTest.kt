package net.prototux.lapin

import net.prototux.lapin.audio.Tones
import net.prototux.lapin.tools.ToolSpecs
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolSpecsTest {
    /** Names the server already uses: its tools would shadow ours. */
    private val serverTools = setOf(
        "announce", "calculate", "cancel_timer", "convert_units", "forget", "get_time", "get_weather", "ha_find",
        "jellyfin_next_up", "list_devices", "list_stations", "list_timers", "media_control", "now_playing", "play_radio",
        "read_page", "recall", "remember", "send_message", "set_timer", "set_alarm", "set_volume", "stop_audio",
        "stop_media", "subsonic_search", "subsonic_play", "jellyfin_play_music", "web_search", "wikipedia",
    )

    @Test fun phoneTools() {
        for (fr in listOf(true, false)) {
            val tools = ToolSpecs.phone(fr)
            val names = tools.map { it.getString("name") }
            assertEquals(names.size, names.toSet().size)
            for (t in tools) {
                val n = t.getString("name")
                assertTrue(n, ToolSpecs.NAME.matches(n))
                assertFalse("$n collides with a server tool", n in serverTools)
                assertTrue(n, t.getString("description").length <= 500)
                val p = t.getJSONObject("parameters")
                assertEquals("object", p.getString("type"))
                val props = p.getJSONObject("properties")
                val req = p.getJSONArray("required")
                for (i in 0 until req.length()) assertTrue("$n: ${req.getString(i)}", props.has(req.getString(i)))
                if (t.getBoolean("confirm")) assertTrue(n, t.getString("summary").isNotEmpty())
            }
            val byName = tools.associateBy { it.getString("name") }
            assertTrue(byName.getValue("send_phone_message").getBoolean("confirm"))
            assertTrue(byName.getValue("call_contact").getBoolean("confirm"))
            assertTrue(byName.getValue("open_app").getBoolean("silent"))
            assertTrue(byName.getValue("flashlight").getBoolean("silent"))
            assertTrue(byName.getValue("send_phone_message").getString("summary").contains("{message}"))
            // music in the phone's own player
            val music = byName.getValue("phone_play_music")
            assertTrue(music.getBoolean("silent"))
            assertFalse(music.getBoolean("confirm"))
            val replaces = music.getJSONArray("replaces")
            assertEquals(listOf("subsonic_play", "jellyfin_play_music"), (0 until replaces.length()).map { replaces.getString(it) })
            val mp = music.getJSONObject("parameters").getJSONObject("properties")
            assertTrue(mp.has("query"))
            val kinds = mp.getJSONObject("kind").getJSONArray("enum")
            assertEquals(listOf("auto", "artist", "album", "playlist", "song", "genre", "random"), (0 until kinds.length()).map { kinds.getString(it) })
            // the protocol's special names for the fast paths
            val media = byName.getValue("phone_media").getJSONObject("parameters")
            val actions = media.getJSONObject("properties").getJSONObject("action").getJSONArray("enum")
            assertEquals(listOf("play", "pause", "next", "previous"), (0 until actions.length()).map { actions.getString(it) })
            assertTrue(byName.getValue("phone_media").getBoolean("silent"))
            assertEquals(0, byName.getValue("phone_now_playing").getJSONObject("parameters").getJSONObject("properties").length())
            // only the music tool replaces server tools
            assertEquals(listOf("phone_play_music"), tools.filter { it.has("replaces") }.map { it.getString("name") })
        }
    }

    @Test fun tvTools() {
        val names = ToolSpecs.tv().map { it.getString("name") }
        assertEquals(listOf("open_app", "phone_media", "phone_now_playing", "open_url"), names)
    }

    @Test fun earcons() {
        for (n in listOf("done", "error", "notify", "wake", "followup", "whatever")) {
            val pcm = Tones.earcon(n)
            assertTrue(n, pcm.size in Tones.RATE / 20..Tones.RATE)       // 50 ms .. 1 s
            assertTrue(n, pcm.any { it.toInt() != 0 })
        }
    }
}

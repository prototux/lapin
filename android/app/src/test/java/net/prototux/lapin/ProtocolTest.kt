package net.prototux.lapin

import net.prototux.lapin.protocol.Proto
import net.prototux.lapin.protocol.ServerMsg
import org.json.JSONArray
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test fun playbackFrame() {
        val pcm = byteArrayOf(1, 2, 3, 4)
        val frame = byteArrayOf(0x02, 0x07, 0x01, 0x00, 0x00) + pcm      // id 263, little endian
        val f = Proto.parsePlayback(frame)!!
        assertEquals(263L, f.streamId)
        assertEquals(5, f.offset)
        assertEquals(4, f.length)
        assertArrayEquals(pcm, frame.copyOfRange(f.offset, f.offset + f.length))
    }

    @Test fun playbackFrameHighId() {
        val f = Proto.parsePlayback(byteArrayOf(0x02, 0xff.toByte(), 0xff.toByte(), 0xff.toByte(), 0xff.toByte()))!!
        assertEquals(4294967295L, f.streamId)
        assertEquals(0, f.length)
    }

    @Test fun notPlayback() {
        assertNull(Proto.parsePlayback(byteArrayOf(0x01, 0, 0, 0, 0, 9)))
        assertNull(Proto.parsePlayback(byteArrayOf(0x02, 0, 0)))
    }

    @Test fun micFrame() {
        val f = Proto.micFrame(byteArrayOf(9, 8, 7), 1, 2)
        assertArrayEquals(byteArrayOf(0x01, 8, 7), f)
        assertEquals(640, Proto.MIC_FRAME_BYTES)
    }

    @Test fun hello() {
        val h = Proto.hello("phone-1", "tok", "phone", "Pixel", " Jason ", "1.0.0", JSONArray(), 40)
        assertEquals("hello", h.getString("type"))
        assertEquals(1, h.getInt("protocol"))
        assertEquals("jason", h.getString("owner"))
        assertEquals(16000, h.getJSONObject("capabilities").getJSONObject("audio_in").getInt("rate"))
        assertTrue(h.getJSONObject("capabilities").getBoolean("confirm_ui"))
        assertEquals(40, h.getJSONObject("settings").getInt("volume"))
    }

    @Test fun outgoing() {
        val w = Proto.wake(3, "followup")
        assertEquals("wake", w.getString("type")); assertEquals(3, w.getInt("wake_id")); assertEquals("followup", w.getString("source"))
        assertEquals("cancel", Proto.button("cancel").getString("action"))
        assertFalse(Proto.text("salut", false).getBoolean("speak"))
        assertTrue(Proto.confirmReply(true).getBoolean("yes"))
        val p = Proto.playback(7, "finished")
        assertEquals("finished", p.getString("event")); assertEquals("finished", p.getString("what"))
    }

    @Test fun parseTurnMessages() {
        assertEquals(ServerMsg.Welcome("Lapin", "Pixel"), ServerMsg.parse("""{"type":"welcome","server":"Lapin","version":"1","config":{"name":"Pixel","room":""}}"""))
        assertEquals(ServerMsg.Pending("wait"), ServerMsg.parse("""{"type":"pending","message":"wait"}"""))
        assertEquals(ServerMsg.Eot(1), ServerMsg.parse("""{"type":"eot","wake_id":1}"""))
        assertEquals(ServerMsg.Cancel(2, "no_speech"), ServerMsg.parse("""{"type":"cancel","wake_id":2,"reason":"no_speech"}"""))
        assertEquals(ServerMsg.Cancel(null, "button"), ServerMsg.parse("""{"type":"cancel","reason":"button"}"""))
        assertEquals(ServerMsg.Transcript("allume", false), ServerMsg.parse("""{"type":"transcript","text":"allume","final":false}"""))
        assertEquals(ServerMsg.Reply("", "done"), ServerMsg.parse("""{"type":"reply","text":"","ack":"done"}"""))
        assertEquals(ServerMsg.Reply("Il est midi.", ""), ServerMsg.parse("""{"type":"reply","text":"Il est midi."}"""))
        assertEquals(ServerMsg.SessionEnd(true, 6000), ServerMsg.parse("""{"type":"session_end","follow_up":true,"follow_up_ms":6000}"""))
        assertEquals(ServerMsg.SessionEnd(false, 6000), ServerMsg.parse("""{"type":"session_end","follow_up":false}"""))
        assertEquals(ServerMsg.Earcon("done", false), ServerMsg.parse("""{"type":"earcon","name":"done"}"""))
        assertEquals(ServerMsg.Set(55, null), ServerMsg.parse("""{"type":"set","volume":55}"""))
        assertEquals(ServerMsg.Listen(6000, true), ServerMsg.parse("""{"type":"listen","timeout_ms":6000,"earcon":true}"""))
        assertEquals(ServerMsg.Confirm("appeler Marie", "call_contact"), ServerMsg.parse("""{"type":"confirm","summary":"appeler Marie","tool":"call_contact"}"""))
    }

    @Test fun parseStreams() {
        assertEquals(ServerMsg.StreamOpen(7, "tts", 24000, 1, 0.0, null),
            ServerMsg.parse("""{"type":"stream_open","id":7,"kind":"tts","rate":24000,"channels":1,"gain_db":0}"""))
        assertEquals(ServerMsg.StreamOpen(8, "media", 48000, 2, -3.0, 300),
            ServerMsg.parse("""{"type":"stream_open","id":8,"kind":"media","rate":48000,"channels":2,"gain_db":-3,"start_at_ns":12,"prebuffer_ms":300}"""))
        assertEquals(ServerMsg.StreamClose(7, true), ServerMsg.parse("""{"type":"stream_close","id":7,"drain":true}"""))
        assertEquals(ServerMsg.StreamClose(7, false), ServerMsg.parse("""{"type":"stream_close","id":7,"drain":false}"""))
        assertEquals(ServerMsg.StreamPause(7, true), ServerMsg.parse("""{"type":"stream_pause","id":7,"paused":true}"""))
    }

    @Test fun parseToolCall() {
        val m = ServerMsg.parse("""{"type":"tool_call","call_id":"c12","name":"start_navigation","args":{"destination":"Gare de Lyon"}}""") as ServerMsg.ToolCall
        assertEquals("c12", m.callId)
        assertEquals("start_navigation", m.name)
        assertEquals("Gare de Lyon", m.args.getString("destination"))
    }

    @Test fun parseOddities() {
        assertNull(ServerMsg.parse("not json"))
        assertNull(ServerMsg.parse("""{"no":"type"}"""))
        assertEquals(ServerMsg.Unknown("led2"), ServerMsg.parse("""{"type":"led2"}"""))
        assertEquals(ServerMsg.Pong, ServerMsg.parse("""{"type":"pong","t0":1,"t1":2}"""))
    }
}

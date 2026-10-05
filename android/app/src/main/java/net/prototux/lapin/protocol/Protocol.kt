package net.prototux.lapin.protocol

import org.json.JSONArray
import org.json.JSONObject

/**
 * The device protocol (docs/PROTOCOL.md): JSON text frames and binary audio frames.
 * Pure Kotlin (plus org.json) so it can be unit tested on the JVM.
 */
object Proto {
    const val VERSION = 1
    const val AUDIO_UP: Byte = 0x01
    const val AUDIO_DOWN: Byte = 0x02

    /** Microphone rate/format: always 16 kHz mono s16le. */
    const val MIC_RATE = 16000
    /** 20 ms of microphone audio. */
    const val MIC_FRAME_BYTES = MIC_RATE / 50 * 2

    // ------------------------------------------------------------------ binary frames
    /** `0x01` + PCM. */
    fun micFrame(pcm: ByteArray, off: Int = 0, len: Int = pcm.size): ByteArray {
        val out = ByteArray(len + 1)
        out[0] = AUDIO_UP
        System.arraycopy(pcm, off, out, 1, len)
        return out
    }

    /** A playback frame: `0x02` + u32 LE stream id + PCM; the PCM is `data[offset until offset+length]`. */
    data class PlaybackFrame(val streamId: Long, val offset: Int, val length: Int)

    fun parsePlayback(data: ByteArray): PlaybackFrame? {
        if (data.size < 5 || data[0] != AUDIO_DOWN) return null
        val id = (data[1].toLong() and 0xff) or
            ((data[2].toLong() and 0xff) shl 8) or
            ((data[3].toLong() and 0xff) shl 16) or
            ((data[4].toLong() and 0xff) shl 24)
        return PlaybackFrame(id, 5, data.size - 5)
    }

    // ------------------------------------------------------------------ device -> server
    fun hello(
        deviceId: String, token: String, kind: String, name: String, owner: String, version: String,
        tools: JSONArray, volume: Int? = null, room: String = "",
    ): JSONObject = JSONObject()
        .put("type", "hello").put("protocol", VERSION)
        .put("device_id", deviceId).put("token", token)
        .put("kind", kind).put("name", name).put("room", room)
        .put("owner", owner.trim().lowercase())
        .put("version", version)
        .put("capabilities", JSONObject()
            .put("audio_in", JSONObject().put("rate", MIC_RATE).put("channels", 1).put("format", "s16le"))
            .put("audio_out", JSONObject()
                .put("rates", JSONArray(listOf(16000, 22050, 24000, 44100, 48000)))
                .put("channels", JSONArray(listOf(1, 2))).put("format", "s16le"))
            .put("confirm_ui", true).put("display", true))
        .put("tools", tools)
        .apply { if (volume != null) put("settings", JSONObject().put("volume", volume).put("mic_muted", false)) }

    fun wake(wakeId: Int, source: String): JSONObject = JSONObject()
        .put("type", "wake").put("wake_id", wakeId).put("source", source).put("score", 1).put("preroll_ms", 0)

    fun audioEnd(wakeId: Int): JSONObject = JSONObject().put("type", "audio_end").put("wake_id", wakeId)
    fun button(action: String): JSONObject = JSONObject().put("type", "button").put("action", action)
    fun text(text: String, speak: Boolean): JSONObject =
        JSONObject().put("type", "text").put("text", text).put("speak", speak)
    fun confirmReply(yes: Boolean): JSONObject = JSONObject().put("type", "confirm_reply").put("yes", yes)
    fun toolResult(callId: String, result: JSONObject): JSONObject =
        JSONObject().put("type", "tool_result").put("call_id", callId).put("result", result)
    fun state(state: String, muted: Boolean = false): JSONObject =
        JSONObject().put("type", "state").put("state", state).put("muted", muted)
    fun settings(volume: Int): JSONObject = JSONObject().put("type", "settings").put("volume", volume)

    /** Playback report. PROTOCOL.md calls the field `event`; the server currently reads `what`: send both. */
    fun playback(id: Long, event: String): JSONObject =
        JSONObject().put("type", "playback").put("id", id).put("event", event).put("what", event)
}

/** A message from the server. */
sealed interface ServerMsg {
    data class Welcome(val server: String, val name: String) : ServerMsg
    data class Pending(val message: String) : ServerMsg
    data class Error(val message: String) : ServerMsg
    data class WakeAck(val wakeId: Int?) : ServerMsg
    data class Eot(val wakeId: Int?) : ServerMsg
    data class Cancel(val wakeId: Int?, val reason: String) : ServerMsg
    data class Transcript(val text: String, val final: Boolean) : ServerMsg
    data class Reply(val text: String, val ack: String) : ServerMsg
    data class SessionEnd(val followUp: Boolean, val followUpMs: Int) : ServerMsg
    data class Earcon(val name: String, val loop: Boolean) : ServerMsg
    data class Stop(val media: Boolean) : ServerMsg
    data class Listen(val timeoutMs: Int, val earcon: Boolean) : ServerMsg
    data class Set(val volume: Int?, val micMuted: Boolean?) : ServerMsg
    data class StreamOpen(
        val id: Long, val kind: String, val rate: Int, val channels: Int,
        val gainDb: Double, val prebufferMs: Int?,
    ) : ServerMsg
    data class StreamClose(val id: Long, val drain: Boolean) : ServerMsg
    data class StreamPause(val id: Long, val paused: Boolean) : ServerMsg
    data class Confirm(val summary: String, val tool: String) : ServerMsg
    data class ToolCall(val callId: String, val name: String, val args: JSONObject) : ServerMsg
    data class Led(val pattern: String) : ServerMsg
    data object Pong : ServerMsg
    data class Unknown(val type: String) : ServerMsg

    companion object {
        private fun JSONObject.optIntOrNull(k: String): Int? =
            if (has(k) && !isNull(k)) optInt(k, Int.MIN_VALUE).takeIf { it != Int.MIN_VALUE } else null

        private fun JSONObject.str(k: String): String = if (isNull(k)) "" else optString(k, "")

        /** Parses one text frame; null if it isn't a JSON object with a type. */
        fun parse(text: String): ServerMsg? {
            val m = try { JSONObject(text) } catch (_: Exception) { return null }
            return parse(m)
        }

        fun parse(m: JSONObject): ServerMsg? {
            val type = m.optString("type", "").ifEmpty { return null }
            return when (type) {
                "welcome" -> Welcome(m.str("server"), m.optJSONObject("config")?.str("name") ?: "")
                "pending" -> Pending(m.str("message"))
                "error" -> Error(m.str("message"))
                "wake_ack" -> WakeAck(m.optIntOrNull("wake_id"))
                "eot" -> Eot(m.optIntOrNull("wake_id"))
                "cancel" -> Cancel(m.optIntOrNull("wake_id"), m.str("reason"))
                "transcript" -> Transcript(m.str("text"), m.optBoolean("final", false))
                "reply" -> Reply(m.str("text"), m.str("ack"))
                "session_end" -> SessionEnd(m.optBoolean("follow_up", false), m.optInt("follow_up_ms", 6000))
                "earcon" -> Earcon(m.str("name"), m.optBoolean("loop", false))
                "stop" -> Stop(m.optBoolean("media", true))
                "listen" -> Listen(m.optInt("timeout_ms", 6000), m.optBoolean("earcon", false))
                "set" -> Set(m.optIntOrNull("volume"), if (m.has("mic_muted")) m.optBoolean("mic_muted") else null)
                "stream_open" -> StreamOpen(
                    m.optLong("id"), m.optString("kind", "tts"), m.optInt("rate", 24000),
                    m.optInt("channels", 1).coerceIn(1, 2), m.optDouble("gain_db", 0.0).let { if (it.isNaN()) 0.0 else it },
                    m.optIntOrNull("prebuffer_ms"),
                )
                "stream_close" -> StreamClose(m.optLong("id"), m.optBoolean("drain", true))
                "stream_pause" -> StreamPause(m.optLong("id"), m.optBoolean("paused", true))
                "confirm" -> Confirm(m.str("summary"), m.str("tool"))
                "tool_call" -> ToolCall(m.str("call_id"), m.str("name"), m.optJSONObject("args") ?: JSONObject())
                "led" -> Led(m.str("pattern"))
                "pong" -> Pong
                else -> Unknown(type)
            }
        }
    }
}

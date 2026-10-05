package net.prototux.lapin.audio

import kotlin.math.PI
import kotlin.math.exp
import kotlin.math.min
import kotlin.math.sin

/** Synthesized earcons (pure Kotlin, unit tested). 16-bit mono PCM. */
object Tones {
    const val RATE = 24000

    private class Note(val freq: Double, val startMs: Int, val lenMs: Int, val gain: Double = 0.35)

    private fun render(notes: List<Note>, tailMs: Int = 60): ShortArray {
        val totalMs = notes.maxOf { it.startMs + it.lenMs } + tailMs
        val out = DoubleArray(RATE * totalMs / 1000)
        for (n in notes) {
            val s0 = RATE * n.startMs / 1000
            val len = RATE * n.lenMs / 1000
            val attack = RATE * 6 / 1000
            for (i in 0 until min(len, out.size - s0)) {
                val t = i.toDouble() / RATE
                val env = min(1.0, i.toDouble() / attack) * exp(-4.5 * i / len)
                // sine + a soft octave partial: a bell-ish, pleasant tone
                val v = sin(2 * PI * n.freq * t) + 0.18 * sin(4 * PI * n.freq * t)
                out[s0 + i] += n.gain * env * v
            }
        }
        return ShortArray(out.size) { (out[it].coerceIn(-1.0, 1.0) * 32000).toInt().toShort() }
    }

    /** The PCM of an earcon by name (PROTOCOL.md: done, error, notify, wake...). */
    fun earcon(name: String): ShortArray = when (name) {
        // "OK": two rising notes (E6 -> A6), a perfect fourth
        "done", "success", "ok" -> render(listOf(Note(1318.5, 0, 160), Note(1760.0, 110, 260)))
        "error", "fail" -> render(listOf(Note(587.3, 0, 180, 0.3), Note(440.0, 150, 280, 0.3)))
        "notify", "message" -> render(listOf(Note(1046.5, 0, 140), Note(1318.5, 110, 140), Note(1568.0, 220, 260)))
        // start of listening: one short soft rising blip
        "wake", "listen" -> render(listOf(Note(880.0, 0, 70, 0.22), Note(1174.7, 45, 110, 0.22)), 20)
        "followup" -> render(listOf(Note(1174.7, 0, 110, 0.16)), 20)
        "cancel", "stop" -> render(listOf(Note(1174.7, 0, 90, 0.2), Note(880.0, 60, 140, 0.2)), 20)
        else -> render(listOf(Note(1046.5, 0, 160, 0.25)))
    }
}

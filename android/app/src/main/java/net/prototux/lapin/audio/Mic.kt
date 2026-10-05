package net.prototux.lapin.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.util.Log
import net.prototux.lapin.TAG
import net.prototux.lapin.protocol.Proto
import kotlin.math.sqrt

/** Where microphone audio comes from: the phone (AudioRecord) or the car (CarAudioRecord). 16 kHz mono s16le. */
interface MicSource {
    fun start(): Boolean
    /** Blocking read; returns the byte count or a negative error. */
    fun read(buf: ByteArray, off: Int, len: Int): Int
    fun stop()
}

/**
 * The phone's microphone. VOICE_COMMUNICATION gets the phone's call processing
 * (multi-mic noise and far-talker suppression on most phones) without touching
 * the audio mode, so answers still play on the speaker. Echo canceller and
 * noise suppressor on; automatic gain control OFF: it lifts room noise in the
 * pauses to speech level and the server can't find the end of the sentence.
 */
class AudioRecordSource : MicSource {
    private var rec: AudioRecord? = null
    private val effects = mutableListOf<AudioEffect>()

    @SuppressLint("MissingPermission")    // checked by the caller
    private fun open(source: Int): AudioRecord? {
        val min = AudioRecord.getMinBufferSize(Proto.MIC_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val r = try {
            AudioRecord(source, Proto.MIC_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(min, Proto.MIC_FRAME_BYTES * 10))
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord($source): $e"); return null
        }
        if (r.state != AudioRecord.STATE_INITIALIZED) { r.release(); return null }
        return r
    }

    override fun start(): Boolean {
        var source = MediaRecorder.AudioSource.VOICE_COMMUNICATION
        val r = open(source) ?: run {
            source = MediaRecorder.AudioSource.VOICE_RECOGNITION
            open(source)
        } ?: return false
        val sid = r.audioSessionId
        try {
            if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(sid)?.let { it.enabled = true; effects += it }
            if (NoiseSuppressor.isAvailable()) NoiseSuppressor.create(sid)?.let { it.enabled = true; effects += it }
            // some phones turn AGC on by default for VOICE_COMMUNICATION: switch it off explicitly
            if (AutomaticGainControl.isAvailable()) AutomaticGainControl.create(sid)?.let { it.enabled = false; effects += it }
        } catch (e: Exception) {
            Log.w(TAG, "audio effects: $e")
        }
        try { r.startRecording() } catch (e: Exception) { release(r); return false }
        if (r.recordingState != AudioRecord.RECORDSTATE_RECORDING) { release(r); return false }
        Log.i(TAG, "mic: " + (if (source == MediaRecorder.AudioSource.VOICE_COMMUNICATION) "VOICE_COMMUNICATION" else "VOICE_RECOGNITION") +
            ", effects " + effects.joinToString { "${it.javaClass.simpleName}=${it.enabled}" })
        rec = r
        return true
    }

    private fun release(r: AudioRecord) {
        effects.forEach { runCatching { it.release() } }
        effects.clear()
        r.release()
    }

    override fun read(buf: ByteArray, off: Int, len: Int): Int = rec?.read(buf, off, len) ?: -1

    override fun stop() {
        val r = rec ?: return
        rec = null
        runCatching { r.stop() }
        release(r)
    }
}

/** Reads 20 ms frames from a [MicSource] on its own thread. */
class Mic(
    private val source: MicSource,
    private val onFrame: (ByteArray) -> Unit,
    private val onLevel: (Float) -> Unit,
    private val onError: () -> Unit,
) : Thread("lapin-mic") {
    @Volatile private var running = true

    override fun run() {
        if (!source.start()) { onError(); return }
        val buf = ByteArray(Proto.MIC_FRAME_BYTES)
        try {
            while (running) {
                var got = 0
                while (got < buf.size && running) {
                    val n = source.read(buf, got, buf.size - got)
                    if (n < 0) { if (running) onError(); return }
                    if (n == 0) sleep(5)
                    got += n
                }
                if (!running) break
                onFrame(buf.copyOf())
                onLevel(level(buf))
            }
        } finally {
            source.stop()
            onLevel(0f)
        }
    }

    fun shutdown() { running = false }

    companion object {
        /** 0..1 loudness for the UI (RMS, log-ish). */
        fun level(pcm: ByteArray, off: Int = 0, len: Int = pcm.size): Float {
            var sum = 0.0
            var n = 0
            var i = off
            while (i + 1 < off + len) {
                val s = ((pcm[i + 1].toInt() shl 8) or (pcm[i].toInt() and 0xff)).toShort().toDouble()
                sum += s * s; n++; i += 2
            }
            if (n == 0) return 0f
            val rms = sqrt(sum / n) / 32768.0
            return (rms * 6).coerceIn(0.0, 1.0).let { sqrt(it) }.toFloat()
        }
    }
}

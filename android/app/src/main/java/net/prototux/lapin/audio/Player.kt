package net.prototux.lapin.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import net.prototux.lapin.TAG
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.pow

/**
 * Playback streams (PROTOCOL.md §3): one AudioTrack per `stream_open`,
 * fed by `0x02` frames, released after `stream_close` (draining or not).
 * Music is ducked by ~18 dB under speech and ~40 dB while the mic is open.
 */
class Player(private val onEvent: (id: Long, event: String) -> Unit) {
    private val main = Handler(Looper.getMainLooper())
    private val streams = ConcurrentHashMap<Long, Stream>()
    private val speechCount = AtomicInteger(0)
    private val idleWaiters = mutableListOf<() -> Unit>()     // main thread only

    /** Usage for speech: the assistant channel on the phone, media in the car. */
    @Volatile var speechUsage: Int = AudioAttributes.USAGE_ASSISTANT

    private val _level = MutableStateFlow(0f)
    val level: StateFlow<Float> = _level

    @Volatile private var micOpen = false

    val speechActive: Boolean get() = speechCount.get() > 0
    val anyActive: Boolean get() = streams.isNotEmpty()
    val mediaActive: Boolean get() = streams.values.any { it.isMedia }

    fun open(id: Long, kind: String, rate: Int, channels: Int, gainDb: Double, prebufferMs: Int?) {
        streams.remove(id)?.drop()
        val s = try { Stream(id, kind, rate, channels, gainDb, prebufferMs ?: 150) } catch (e: Exception) {
            Log.w(TAG, "stream $id: $e"); return
        }
        streams[id] = s
        if (!s.isMedia) speechCount.incrementAndGet()
        updateDucking()
        s.start()
    }

    fun write(id: Long, data: ByteArray, off: Int, len: Int) {
        streams[id]?.feed(data.copyOfRange(off, off + len))
    }

    fun close(id: Long, drain: Boolean) {
        val s = streams[id] ?: return
        if (drain) s.finish() else s.drop()
    }

    fun pause(id: Long, paused: Boolean) { streams[id]?.paused = paused }

    /** Barge-in / dismiss: drop all speech now (music keeps playing). */
    fun stopSpeech() = streams.values.filter { !it.isMedia }.forEach { it.drop() }

    /** The user said "stop". */
    fun stopAll() = streams.values.forEach { it.drop() }

    fun setMicOpen(open: Boolean) { micOpen = open; updateDucking() }

    /** Runs [cb] on the main thread once no speech stream is left (now if none). */
    fun whenSpeechIdle(cb: () -> Unit) {
        if (!speechActive) { main.post(cb); return }
        main.post { if (!speechActive) cb() else idleWaiters += cb }
    }

    private fun released(s: Stream) {
        if (streams.remove(s.sid, s) && !s.isMedia) speechCount.decrementAndGet()
        updateDucking()
        main.post {
            if (!speechActive && idleWaiters.isNotEmpty()) {
                val w = idleWaiters.toList(); idleWaiters.clear(); w.forEach { it() }
            }
        }
    }

    private fun updateDucking() {
        val duck = when { micOpen -> 0.01f; speechActive -> 0.125f; else -> 1f }
        streams.values.forEach { it.duck = if (it.isMedia) duck else 1f }
    }

    private inner class Stream(
        val sid: Long, kind: String, val rate: Int, val channels: Int, gainDb: Double, prebufferMs: Int,
    ) : Thread("lapin-play-$sid") {
        val isMedia = kind == "media"
        private val gain = 10.0.pow(gainDb / 20).toFloat().coerceIn(0f, 1f)
        private val bytesPerFrame = 2 * channels
        private val prebufferBytes = rate * bytesPerFrame * prebufferMs.coerceIn(0, 2000) / 1000
        private val queue = LinkedBlockingQueue<ByteArray>()
        private val queued = AtomicInteger(0)
        @Volatile private var closed = false
        @Volatile private var dropped = false
        @Volatile var paused = false
        @Volatile var duck = 1f
            set(v) { field = v; runCatching { track.setVolume(gain * v) } }

        private val track: AudioTrack = run {
            val usage = when (kind) {
                "media" -> AudioAttributes.USAGE_MEDIA
                "alarm" -> AudioAttributes.USAGE_ALARM
                else -> speechUsage
            }
            val content = if (kind == "media") AudioAttributes.CONTENT_TYPE_MUSIC else AudioAttributes.CONTENT_TYPE_SPEECH
            val mask = if (channels == 2) AudioFormat.CHANNEL_OUT_STEREO else AudioFormat.CHANNEL_OUT_MONO
            val min = AudioTrack.getMinBufferSize(rate, mask, AudioFormat.ENCODING_PCM_16BIT)
            AudioTrack.Builder()
                .setAudioAttributes(AudioAttributes.Builder().setUsage(usage).setContentType(content).build())
                .setAudioFormat(AudioFormat.Builder().setSampleRate(rate).setChannelMask(mask)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                .setTransferMode(AudioTrack.MODE_STREAM)
                .setBufferSizeInBytes(maxOf(min, rate * bytesPerFrame / 4))
                .build()
        }

        fun feed(b: ByteArray) {
            if (closed || dropped) return
            queued.addAndGet(b.size)
            queue.put(b)
        }

        fun finish() { closed = true }
        fun drop() { dropped = true; closed = true; interrupt() }

        override fun run() {
            var written = 0L
            try {
                track.setVolume(gain * duck)
                // a little prebuffer so speech doesn't stutter at the start
                val t0 = SystemClock.elapsedRealtime()
                while (!dropped && !closed && queued.get() < prebufferBytes && SystemClock.elapsedRealtime() - t0 < 3000) sleep(10)
                if (dropped) return
                track.play()
                onEvent(sid, "started")
                while (!dropped) {
                    val b = queue.poll(40, TimeUnit.MILLISECONDS)
                    if (b == null) { if (closed && queue.isEmpty()) break else continue }
                    queued.addAndGet(-b.size)
                    while (paused && !dropped) {
                        if (track.playState == AudioTrack.PLAYSTATE_PLAYING) track.pause()
                        sleep(20)
                    }
                    if (track.playState == AudioTrack.PLAYSTATE_PAUSED) track.play()
                    var off = 0
                    val len = b.size - b.size % bytesPerFrame
                    while (off < len && !dropped) {
                        val n = track.write(b, off, len - off)
                        if (n <= 0) break
                        off += n
                    }
                    written += off
                    if (!isMedia) _level.value = Mic.level(b)
                }
                if (!dropped) {
                    // push the tail out with silence, then wait until everything real was heard
                    val frames = written / bytesPerFrame
                    val silence = ByteArray(track.bufferSizeInFrames * bytesPerFrame)
                    track.write(silence, 0, silence.size)
                    val deadline = SystemClock.elapsedRealtime() + 2000 + frames * 1000 / rate
                    while (!dropped && (track.playbackHeadPosition.toLong() and 0xffffffffL) < frames &&
                        SystemClock.elapsedRealtime() < deadline) sleep(20)
                }
            } catch (_: InterruptedException) {
            } catch (e: Exception) {
                Log.w(TAG, "stream $sid: $e")
            } finally {
                runCatching { if (dropped) { track.pause(); track.flush() }; track.stop() }
                track.release()
                if (!isMedia) _level.value = 0f
                onEvent(sid, if (dropped) "stopped" else "finished")
                released(this)
            }
        }
    }
}

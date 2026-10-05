package net.prototux.lapin.audio

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.util.Log
import net.prototux.lapin.TAG
import java.util.concurrent.Executors

/** Plays short synthesized sounds. */
class Earcons {
    private val exec = Executors.newSingleThreadExecutor()
    private val cache = HashMap<String, ShortArray>()

    fun play(name: String) {
        exec.execute {
            try {
                val pcm = synchronized(cache) { cache.getOrPut(name) { Tones.earcon(name) } }
                val track = AudioTrack.Builder()
                    .setAudioAttributes(AudioAttributes.Builder()
                        .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION)
                        .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build())
                    .setAudioFormat(AudioFormat.Builder().setSampleRate(Tones.RATE)
                        .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
                    .setTransferMode(AudioTrack.MODE_STATIC)
                    .setBufferSizeInBytes(pcm.size * 2)
                    .build()
                track.write(pcm, 0, pcm.size)
                track.play()
                Thread.sleep(pcm.size * 1000L / Tones.RATE + 80)
                track.release()
            } catch (e: Exception) {
                Log.w(TAG, "earcon $name: $e")
            }
        }
    }
}

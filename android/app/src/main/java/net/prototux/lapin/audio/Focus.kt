package net.prototux.lapin.audio

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager

/** Transient audio focus while the assistant listens or speaks (other apps duck). */
class Focus(context: Context) {
    private val am = context.getSystemService(AudioManager::class.java)
    private var req: AudioFocusRequest? = null

    fun request(exclusive: Boolean = false) {
        if (req != null) return
        val r = AudioFocusRequest.Builder(
            if (exclusive) AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_EXCLUSIVE else AudioManager.AUDIOFOCUS_GAIN_TRANSIENT_MAY_DUCK)
            .setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setOnAudioFocusChangeListener { }
            .build()
        am.requestAudioFocus(r)
        req = r
    }

    fun abandon() {
        req?.let { am.abandonAudioFocusRequest(it) }
        req = null
    }
}

package net.prototux.lapin.voice

import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.util.Log
import net.prototux.lapin.TAG

/**
 * Makes Lapin selectable as the "Digital assistant app". The system binds it
 * while Lapin is the default assistant; each activation (assistant key,
 * long-press home/power) opens a [LapinSession] via [LapinSessionService].
 */
class LapinVoiceInteractionService : VoiceInteractionService() {
    override fun onReady() {
        super.onReady()
        instance = this
        Log.i(TAG, "voice interaction service ready")
    }

    override fun onShutdown() {
        instance = null
        super.onShutdown()
    }

    companion object {
        @Volatile var instance: LapinVoiceInteractionService? = null

        /** Opens the assistant card as if the assistant key was pressed (only while Lapin is the default assistant). */
        fun show(): Boolean {
            val s = instance ?: return false
            s.showSession(Bundle(), 0)
            return true
        }
    }
}
